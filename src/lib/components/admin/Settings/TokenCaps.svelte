<script lang="ts">
	/**
	 * TokenCaps
	 * ---------
	 * Per-target token usage caps. Targets can be a user, group, model,
	 * or an API key; window sizes are in millions of tokens (1 = 1M).
	 *
	 * Behavior contract after this refactor:
	 *   • Edits live in the local `caps` array until the admin hits Save
	 *     (no autosave / debounce — that path was removed because typed
	 *     numbers fired many change events and the saved-vs-input drift
	 *     was confusing).
	 *   • The Save button is disabled until `dirty` becomes true.
	 *   • The target_id field uses `SearchCombobox` so admins can search
	 *     by name rather than pasting opaque ids; api_key stays a plain
	 *     input because there's no admin-list endpoint for those.
	 *   • Removing a populated row asks for confirmation (data loss);
	 *     removing an empty row is silent.
	 *   • Rows with empty `target_id` get a dashed border + lower
	 *     opacity so it's obvious they aren't valid yet.
	 */
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { models } from '$lib/stores';
	import {
		getTokenCaps,
		setTokenCaps,
		getApiKeyTokenUsage,
		getEndpointTokenUsage,
		type TokenCap,
		type ApiKeyTokenUsageResponse,
		type EndpointTokenUsageResponse
	} from '$lib/apis/configs';
	import { searchUsers } from '$lib/apis/users';
	import { searchGroups } from '$lib/apis/groups';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import SearchCombobox from '$lib/components/common/SearchCombobox.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	const TARGET_TYPES: TokenCap['target_type'][] = ['user', 'group', 'model', 'api_key'];

	// Working copy. Persisted as `saved` snapshot for dirty detection.
	let caps: TokenCap[] = [];
	let saved: TokenCap[] = [];
	let loaded = false;
	let saving = false;
	let savingNoChange: boolean = false;

	// Analytics (API-path token usage, not part of the chat dashboard).
	let apiKeyUsage: ApiKeyTokenUsageResponse | null = null;
	let endpointUsage: EndpointTokenUsageResponse | null = null;

	// Confirm-on-delete state. The pending row index is captured at click
	// time so the modal can title/reference it correctly.
	let pendingRemoveIdx: number | null = null;
	let showRemoveConfirm: boolean = false;

	// --- SearchCombobox endpoints ---

	// Users + groups use real search endpoints; models come from the
	// already-loaded $models store (no network). All three return
	// SearchCombobox's `{id, label}[]` shape.
	async function userSearch(q: string): Promise<{ id: string; label: string }[]> {
		try {
			const res: any = await searchUsers(localStorage.token, q);
			const arr: any[] = res?.users ?? [];
			return arr.map((u: any) => ({
				id: u.id,
				label: u.name ? `${u.name}${u.email ? ` (${u.email})` : ''}` : (u.email ?? u.id)
			}));
		} catch (e) {
			return [];
		}
	}

	async function groupSearch(q: string): Promise<{ id: string; label: string }[]> {
		try {
			const res: any = await searchGroups(localStorage.token, q);
			const arr: any[] = Array.isArray(res) ? res : (res?.items ?? res?.groups ?? []);
			return arr.map((g: any) => ({ id: g.id, label: g.name ?? g.id }));
		} catch (e) {
			return [];
		}
	}

	// $models is a store; reactive access happens at template-time. We
	// build the function lazily from the current snapshot so typing into
	// the combobox re-reads the current store value without us needing to
	// tear down the closure.
	function modelSearchEndpoint(): (q: string) => Promise<{ id: string; label: string }[]> {
		return async (q: string) => {
			const all = ($models ?? []).map((m: any) => ({
				id: m.id,
				label: m.name ?? m.id
			}));
			const needle = q.toLowerCase().trim();
			if (!needle) return all.slice(0, 50);
			return all
				.filter(
					(m) => m.id.toLowerCase().includes(needle) || m.label.toLowerCase().includes(needle)
				)
				.slice(0, 50);
		};
	}

	// --- Dirty detection ---

	// JSON round-trip avoids the trap of "two arrays with same values but
	// different reference equality === dirty" semantics. Deep-equal is
	// what the admin expects.
	$: dirty =
		loaded && (JSON.stringify(caps) !== JSON.stringify(saved) || caps.length !== saved.length);

	// --- Sanitization + save ---

	function sanitizedCaps(): TokenCap[] {
		// Drop half-configured rows (empty target_id). Same contract as
		// the autosave version — backend only sees committed rows.
		return caps
			.filter((c) => c.target_id)
			.map((c) => ({
				target_type: c.target_type,
				target_id: c.target_id,
				hourly_millions: Number(c.hourly_millions) || 0,
				daily_millions: Number(c.daily_millions) || 0,
				weekly_millions: Number(c.weekly_millions) || 0,
				monthly_millions: Number(c.monthly_millions) || 0
			}));
	}

	async function save() {
		if (!dirty || saving) return;
		saving = true;
		try {
			const res = await setTokenCaps(localStorage.token, { caps: sanitizedCaps() });
			caps = res.caps;
			saved = JSON.parse(JSON.stringify(res.caps));
			toast.success($i18n.t('Token caps saved'));
		} catch (e) {
			console.error('Failed to save token caps:', e);
			toast.error($i18n.t('Failed to save token caps'));
		} finally {
			saving = false;
		}
	}

	// --- Row operations ---

	function addCap() {
		caps = [
			...caps,
			{
				target_type: 'user',
				target_id: '',
				hourly_millions: 0,
				daily_millions: 0,
				weekly_millions: 0,
				monthly_millions: 0
			}
		];
	}

	// Populated rows go through a confirm modal; empty rows die silently
	// (they're never persisted in the first place — the sanitizer drops
	// them).
	function requestRemove(idx: number) {
		const cap = caps[idx];
		if (!cap) return;
		if (!cap.target_id) {
			caps = caps.filter((_, i) => i !== idx);
			return;
		}
		pendingRemoveIdx = idx;
	}

	function confirmRemove() {
		if (pendingRemoveIdx === null) return;
		caps = caps.filter((_, i) => i !== pendingRemoveIdx);
		pendingRemoveIdx = null;
	}

	function cancelRemove() {
		pendingRemoveIdx = null;
	}

	// Switching target type changes the id namespace — clear the id so
	// a stale user id can't masquerade as a group/model/api_key id.
	function setTargetType(idx: number, targetType: TokenCap['target_type']) {
		caps = caps.map((c, i) => (i === idx ? { ...c, target_type: targetType, target_id: '' } : c));
	}

	function setTargetId(idx: number, newId: string) {
		caps = caps.map((c, i) => (i === idx ? { ...c, target_id: newId } : c));
	}

	function setWindow(idx: number, field: keyof TokenCap, raw: number | null) {
		if (!caps[idx]) return;
		const parsed = raw === null || Number.isNaN(raw) ? 0 : Math.max(0, raw);
		caps = caps.map((c, i) => (i === idx ? { ...c, [field]: parsed } : c));
	}

	// --- Analytics ---

	async function loadAnalytics() {
		try {
			apiKeyUsage = await getApiKeyTokenUsage(localStorage.token, 50);
		} catch (e) {
			console.error('Failed to load API key token usage:', e);
		}
		try {
			endpointUsage = await getEndpointTokenUsage(localStorage.token);
		} catch (e) {
			console.error('Failed to load endpoint token usage:', e);
		}
	}

	const fmt = (n: number | null | undefined) => (n ?? 0).toLocaleString();

	// Backend sorts by total tokens, but sort client-side too so the
	// ordering survives any future response-shape drift.
	$: sortedApiKeyUsage = apiKeyUsage
		? [...apiKeyUsage.keys].sort((a, b) => (b.total_tokens ?? 0) - (a.total_tokens ?? 0))
		: [];
	$: sortedEndpointUsage = endpointUsage
		? [...endpointUsage.endpoints].sort((a, b) => (b.total_tokens ?? 0) - (a.total_tokens ?? 0))
		: [];

	onMount(async () => {
		try {
			const res = await getTokenCaps(localStorage.token);
			caps = res.caps;
			saved = JSON.parse(JSON.stringify(res.caps));
		} catch (e) {
			console.error('Failed to load token caps:', e);
			toast.error($i18n.t('Failed to load token caps'));
		}
		await loadAnalytics();
		loaded = true;
	});
</script>

<form class="flex h-full flex-col justify-between text-sm" on:submit|preventDefault={save}>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('Token Caps')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		{#if !loaded}
			<div class="flex justify-center py-6">
				<Spinner />
			</div>
		{:else}
			<AdminSettingSection title={$i18n.t('Caps')} first>
				<p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
					{$i18n.t(
						'Per-target token usage caps. Values are in millions of tokens (1 = 1M tokens); 0 means unlimited. An API key cap is summed with its owning user cap — usage against a key counts against both.'
					)}
				</p>

				{#if caps.length === 0}
					<div class="text-xs text-gray-400 dark:text-gray-500 py-4">
						{$i18n.t(
							'No caps configured. Add a cap to limit token usage per user, group, model, or API key.'
						)}
					</div>
				{:else}
					<div class="flex flex-col gap-1.5 my-2">
						{#each caps as cap, idx (idx)}
							{@const isEmpty = !cap.target_id}
							<div
								class="rounded-lg border px-3 py-2 transition-colors {isEmpty
									? 'border-dashed border-gray-300 dark:border-gray-700 bg-gray-50/30 dark:bg-gray-900/30 opacity-60'
									: 'border-gray-200 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-850/50'}"
							>
								<div class="flex items-center gap-2 flex-wrap">
									<!-- Target type -->
									<select
										class="shrink-0 w-24 text-xs bg-transparent outline-none"
										value={cap.target_type}
										on:change={(e) =>
											setTargetType(
												idx,
												(e.currentTarget as HTMLSelectElement).value as TokenCap['target_type']
											)}
										aria-label={$i18n.t('Target type')}
									>
										{#each TARGET_TYPES as t (t)}
											<option value={t}>{t}</option>
										{/each}
									</select>

									<!-- Target id: SearchCombobox for user/group/model; plain input for api_key. -->
									<div class="flex-1 min-w-40">
										{#if cap.target_type === 'api_key'}
											<input
												class="w-full text-xs bg-transparent outline-none border-b border-gray-200 dark:border-gray-700 focus:border-gray-400 dark:focus:border-gray-500 py-0.5"
												placeholder={$i18n.t('Paste API key id')}
												value={cap.target_id}
												on:change={(e) =>
													setTargetId(idx, (e.currentTarget as HTMLInputElement).value)}
												aria-label={$i18n.t('Target id')}
											/>
										{:else if cap.target_type === 'model'}
											<SearchCombobox
												value={cap.target_id}
												placeholder={$i18n.t('Search models')}
												endpoint={modelSearchEndpoint()}
												onChange={(id) => setTargetId(idx, id)}
											/>
										{:else if cap.target_type === 'user'}
											<SearchCombobox
												value={cap.target_id}
												placeholder={$i18n.t('Search users')}
												endpoint={userSearch}
												onChange={(id) => setTargetId(idx, id)}
											/>
										{:else}
											<SearchCombobox
												value={cap.target_id}
												placeholder={$i18n.t('Search groups')}
												endpoint={groupSearch}
												onChange={(id) => setTargetId(idx, id)}
											/>
										{/if}
									</div>

									<!-- Window caps in millions of tokens -->
									<div class="flex items-center gap-1 shrink-0">
										<input
											type="number"
											min="0"
											step="0.1"
											class="w-16 text-xs text-right bg-transparent outline-none border border-gray-200 dark:border-gray-700 rounded px-1.5 py-0.5"
											placeholder="0"
											title={$i18n.t('Hourly cap in millions of tokens. 0 = unlimited.')}
											value={cap.hourly_millions}
											on:change={(e) =>
												setWindow(
													idx,
													'hourly_millions',
													(e.currentTarget as HTMLInputElement).valueAsNumber
												)}
											aria-label={$i18n.t('Hourly (M)')}
										/>
										<input
											type="number"
											min="0"
											step="0.1"
											class="w-16 text-xs text-right bg-transparent outline-none border border-gray-200 dark:border-gray-700 rounded px-1.5 py-0.5"
											placeholder="0"
											title={$i18n.t('Daily cap in millions of tokens. 0 = unlimited.')}
											value={cap.daily_millions}
											on:change={(e) =>
												setWindow(
													idx,
													'daily_millions',
													(e.currentTarget as HTMLInputElement).valueAsNumber
												)}
											aria-label={$i18n.t('Daily (M)')}
										/>
										<input
											type="number"
											min="0"
											step="0.1"
											class="w-16 text-xs text-right bg-transparent outline-none border border-gray-200 dark:border-gray-700 rounded px-1.5 py-0.5"
											placeholder="0"
											title={$i18n.t('Weekly cap in millions of tokens. 0 = unlimited.')}
											value={cap.weekly_millions}
											on:change={(e) =>
												setWindow(
													idx,
													'weekly_millions',
													(e.currentTarget as HTMLInputElement).valueAsNumber
												)}
											aria-label={$i18n.t('Weekly (M)')}
										/>
										<input
											type="number"
											min="0"
											step="0.1"
											class="w-16 text-xs text-right bg-transparent outline-none border border-gray-200 dark:border-gray-700 rounded px-1.5 py-0.5"
											placeholder="0"
											title={$i18n.t('Monthly cap in millions of tokens. 0 = unlimited.')}
											value={cap.monthly_millions}
											on:change={(e) =>
												setWindow(
													idx,
													'monthly_millions',
													(e.currentTarget as HTMLInputElement).valueAsNumber
												)}
											aria-label={$i18n.t('Monthly (M)')}
										/>
									</div>

									<!-- Remove -->
									<button
										type="button"
										class="p-1 shrink-0 text-gray-400 hover:text-red-500"
										on:click={() => requestRemove(idx)}
										aria-label={isEmpty ? $i18n.t('Remove empty row') : $i18n.t('Remove cap')}
										title={isEmpty ? $i18n.t('Remove empty row') : $i18n.t('Remove cap')}
									>
										<svg viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
											<path
												d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
											/>
										</svg>
									</button>
								</div>
								<div class="mt-1 text-[0.6875rem] text-gray-400 dark:text-gray-600 pl-26">
									{#if isEmpty}
										<span class="text-amber-600 dark:text-amber-400">
											{$i18n.t('Pick a target to enable this row.')}
										</span>
									{:else}
										{$i18n.t(
											'Hourly (M) / Daily (M) / Weekly (M) / Monthly (M) — 1 = 1M tokens, 0 = unlimited'
										)}
									{/if}
								</div>
							</div>
						{/each}
					</div>
				{/if}

				<button
					type="button"
					on:click={addCap}
					class="w-full text-sm border border-dashed border-gray-300 dark:border-gray-700 rounded-lg py-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:border-gray-400 dark:hover:border-gray-600 transition"
				>
					+ {$i18n.t('Add cap')}
				</button>
			</AdminSettingSection>

			<!-- ===== Usage analytics (unchanged from autosave version) ===== -->
			<AdminSettingSection title={$i18n.t('Usage')}>
				<p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
					{$i18n.t(
						'Token usage recorded on the OpenAI-compatible API path (API-key traffic). Chat usage is tracked separately on the Analytics page.'
					)}
				</p>

				<!-- Top API keys by token usage -->
				<div class="mb-6">
					<div class="flex items-center justify-between mb-2">
						<h4 class="text-xs font-medium text-gray-600 dark:text-gray-400">
							{$i18n.t('Top API keys by token usage')}
						</h4>
						<button
							type="button"
							class="text-xs text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
							on:click={loadAnalytics}
						>
							{$i18n.t('Refresh')}
						</button>
					</div>

					{#if apiKeyUsage === null}
						<div class="text-xs text-gray-400 dark:text-gray-500 py-2">{$i18n.t('Loading...')}</div>
					{:else if sortedApiKeyUsage.length === 0}
						<div class="text-xs text-gray-400 dark:text-gray-500 py-2">
							{$i18n.t('No API token usage recorded yet.')}
						</div>
					{:else}
						<div class="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-800">
							<table class="w-full text-xs">
								<thead class="bg-gray-50 dark:bg-gray-850 text-gray-500 dark:text-gray-400">
									<tr>
										<th class="px-3 py-1.5 text-left font-medium">API key</th>
										<th class="px-3 py-1.5 text-right font-medium">Prompt tokens</th>
										<th class="px-3 py-1.5 text-right font-medium">Completion tokens</th>
										<th class="px-3 py-1.5 text-right font-medium">Total tokens</th>
										<th class="px-3 py-1.5 text-right font-medium">Requests</th>
									</tr>
								</thead>
								<tbody class="divide-y divide-gray-100 dark:divide-gray-850">
									{#each sortedApiKeyUsage as k (k.api_key_id)}
										<tr>
											<td class="px-3 py-1.5 font-mono text-[0.6875rem] truncate max-w-48"
												>{k.api_key_id}</td
											>
											<td class="px-3 py-1.5 text-right tabular-nums">{fmt(k.prompt_tokens)}</td>
											<td class="px-3 py-1.5 text-right tabular-nums">{fmt(k.completion_tokens)}</td
											>
											<td class="px-3 py-1.5 text-right tabular-nums font-medium"
												>{fmt(k.total_tokens)}</td
											>
											<td class="px-3 py-1.5 text-right tabular-nums">{fmt(k.request_count)}</td>
										</tr>
									{/each}
									<tr class="bg-gray-50/70 dark:bg-gray-850/70 font-medium">
										<td class="px-3 py-1.5">{$i18n.t('Total')}</td>
										<td class="px-3 py-1.5 text-right tabular-nums"
											>{fmt(apiKeyUsage.total_prompt_tokens)}</td
										>
										<td class="px-3 py-1.5 text-right tabular-nums"
											>{fmt(apiKeyUsage.total_completion_tokens)}</td
										>
										<td class="px-3 py-1.5 text-right tabular-nums"
											>{fmt(apiKeyUsage.total_tokens)}</td
										>
										<td class="px-3 py-1.5 text-right tabular-nums"
											>{fmt(apiKeyUsage.total_request_count)}</td
										>
									</tr>
								</tbody>
							</table>
						</div>
					{/if}
				</div>

				<!-- API vs UI split -->
				<div>
					<h4 class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-2">
						{$i18n.t('API vs UI split')}
					</h4>

					{#if endpointUsage === null}
						<div class="text-xs text-gray-400 dark:text-gray-500 py-2">{$i18n.t('Loading...')}</div>
					{:else if sortedEndpointUsage.length === 0}
						<div class="text-xs text-gray-400 dark:text-gray-500 py-2">
							{$i18n.t('No API token usage recorded yet.')}
						</div>
					{:else}
						<div class="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-800">
							<table class="w-full text-xs">
								<thead class="bg-gray-50 dark:bg-gray-850 text-gray-500 dark:text-gray-400">
									<tr>
										<th class="px-3 py-1.5 text-left font-medium">Endpoint</th>
										<th class="px-3 py-1.5 text-right font-medium">Prompt tokens</th>
										<th class="px-3 py-1.5 text-right font-medium">Completion tokens</th>
										<th class="px-3 py-1.5 text-right font-medium">Total tokens</th>
										<th class="px-3 py-1.5 text-right font-medium">Requests</th>
									</tr>
								</thead>
								<tbody class="divide-y divide-gray-100 dark:divide-gray-850">
									{#each sortedEndpointUsage as e (e.endpoint)}
										<tr>
											<td class="px-3 py-1.5 font-mono text-[0.6875rem]">{e.endpoint}</td>
											<td class="px-3 py-1.5 text-right tabular-nums">{fmt(e.prompt_tokens)}</td>
											<td class="px-3 py-1.5 text-right tabular-nums">{fmt(e.completion_tokens)}</td
											>
											<td class="px-3 py-1.5 text-right tabular-nums font-medium"
												>{fmt(e.total_tokens)}</td
											>
											<td class="px-3 py-1.5 text-right tabular-nums">{fmt(e.request_count)}</td>
										</tr>
									{/each}
									<tr class="bg-gray-50/70 dark:bg-gray-850/70 font-medium">
										<td class="px-3 py-1.5">{$i18n.t('Total')}</td>
										<td class="px-3 py-1.5 text-right tabular-nums"
											>{fmt(endpointUsage.total_prompt_tokens)}</td
										>
										<td class="px-3 py-1.5 text-right tabular-nums"
											>{fmt(endpointUsage.total_completion_tokens)}</td
										>
										<td class="px-3 py-1.5 text-right tabular-nums"
											>{fmt(endpointUsage.total_tokens)}</td
										>
										<td class="px-3 py-1.5 text-right tabular-nums"
											>{fmt(endpointUsage.total_request_count)}</td
										>
									</tr>
								</tbody>
							</table>
						</div>
					{/if}
				</div>
			</AdminSettingSection>
		{/if}
	</div>

	<div class="flex justify-end pt-3 pb-3">
		<button
			type="button"
			class="px-3.5 py-1.5 text-sm font-medium bg-black dark:bg-white text-white dark:text-black rounded-lg transition disabled:opacity-50"
			on:click={save}
			disabled={!loaded || saving || !dirty}
		>
			{saving ? $i18n.t('Saving...') : $i18n.t('Save')}
		</button>
	</div>
</form>

<ConfirmDialog
	bind:show={showRemoveConfirm}
	title={$i18n.t('Remove this cap?')}
	message={pendingRemoveIdx !== null && caps[pendingRemoveIdx]
		? $i18n.t(
				'This will remove the cap for "{type}/{id}". The change takes effect immediately on save.',
				{
					type: caps[pendingRemoveIdx].target_type,
					id: caps[pendingRemoveIdx].target_id
				}
			)
		: ''}
	confirmLabel={$i18n.t('Remove')}
	onConfirm={confirmRemove}
	on:cancel={cancelRemove}
/>
