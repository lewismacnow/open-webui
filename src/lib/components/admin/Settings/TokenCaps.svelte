<script lang="ts">
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
	import { getUsers } from '$lib/apis/users';
	import { getGroups } from '$lib/apis/groups';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	const TARGET_TYPES: TokenCap['target_type'][] = ['user', 'group', 'model', 'api_key'];

	// The persisted cap list. Values are in millions of tokens
	// (1 = 1M tokens); 0 means unlimited.
	let caps: TokenCap[] = [];
	let loaded = false;
	let saving = false;

	// Debounced autosave (same pattern as the chain editor).
	let saveTimer: ReturnType<typeof setTimeout> | null = null;

	// Autocomplete options for target_id, keyed by target type.
	// api_key is paste-id for now (no admin list endpoint wired client-side).
	let userOptions: { id: string; label: string }[] = [];
	let groupOptions: { id: string; label: string }[] = [];

	// Analytics (API-path token usage, not part of the chat dashboard).
	let apiKeyUsage: ApiKeyTokenUsageResponse | null = null;
	let endpointUsage: EndpointTokenUsageResponse | null = null;

	$: modelOptions = ($models ?? []).map((m: any) => ({ id: m.id, label: m.name ?? m.id }));

	// Backend sorts by total tokens, but sort client-side too so the
	// ordering survives any future response-shape drift.
	$: sortedApiKeyUsage = apiKeyUsage
		? [...apiKeyUsage.keys].sort((a, b) => (b.total_tokens ?? 0) - (a.total_tokens ?? 0))
		: [];
	$: sortedEndpointUsage = endpointUsage
		? [...endpointUsage.endpoints].sort((a, b) => (b.total_tokens ?? 0) - (a.total_tokens ?? 0))
		: [];

	function scheduleSave() {
		if (!loaded) return;
		if (saveTimer) clearTimeout(saveTimer);
		saveTimer = setTimeout(() => {
			saveTimer = null;
			save();
		}, 800);
	}

	// Drop half-configured rows before persisting: entries without a
	// target_id are kept client-side only (mirrors the chain editor's
	// convention). All-zero windows are valid (explicit unlimited).
	function sanitizedCaps(): TokenCap[] {
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
		if (saveTimer) {
			clearTimeout(saveTimer);
			saveTimer = null;
		}
		saving = true;
		try {
			const res = await setTokenCaps(localStorage.token, { caps: sanitizedCaps() });
			caps = res.caps;
			toast.success($i18n.t('Token caps saved'));
		} catch (e) {
			toast.error($i18n.t('Failed to save token caps'));
		} finally {
			saving = false;
		}
	}

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

	function removeCap(idx: number) {
		caps = caps.filter((_, i) => i !== idx);
		scheduleSave();
	}

	// Switching target type changes the id namespace — clear the id so a
	// stale user id can't silently masquerade as a group/model/api key id.
	function setTargetType(idx: number, targetType: TokenCap['target_type']) {
		caps = caps.map((c, i) => (i === idx ? { ...c, target_type: targetType, target_id: '' } : c));
		scheduleSave();
	}

	function setWindow(idx: number, field: keyof TokenCap, raw: number | null) {
		if (!caps[idx]) return;
		const parsed = raw === null || Number.isNaN(raw) ? 0 : Math.max(0, raw);
		caps = caps.map((c, i) => (i === idx ? { ...c, [field]: parsed } : c));
		scheduleSave();
	}

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

	onMount(async () => {
		try {
			const res = await getTokenCaps(localStorage.token);
			caps = res.caps;
		} catch (e) {
			console.error('Failed to load token caps:', e);
			toast.error($i18n.t('Failed to load token caps'));
		}

		// Autocomplete sources. Best-effort — failures just leave the
		// datalist empty (ids can still be pasted).
		try {
			const res = await getUsers(localStorage.token);
			const users = res?.data ?? res ?? [];
			userOptions = users.map((u: any) => ({
				id: u.id,
				label: u.name ? `${u.name} (${u.email ?? u.id})` : (u.email ?? u.id)
			}));
		} catch (e) {
			console.error('Failed to load users for autocomplete:', e);
		}

		try {
			const groups = await getGroups(localStorage.token);
			groupOptions = (groups ?? []).map((g: any) => ({ id: g.id, label: g.name ?? g.id }));
		} catch (e) {
			console.error('Failed to load groups for autocomplete:', e);
		}

		await loadAnalytics();
		loaded = true;
	});
</script>

<!-- Shared autocomplete lists (native datalist keeps this Svelte-4 light) -->
<datalist id="token-caps-user-options">
	{#each userOptions as o (o.id)}<option value={o.id}>{o.label}</option>{/each}
</datalist>
<datalist id="token-caps-group-options">
	{#each groupOptions as o (o.id)}<option value={o.id}>{o.label}</option>{/each}
</datalist>
<datalist id="token-caps-model-options">
	{#each modelOptions as o (o.id)}<option value={o.id}>{o.label}</option>{/each}
</datalist>

<form class="flex h-full flex-col justify-between text-sm" on:submit|preventDefault={() => {}}>
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
							<div
								class="rounded-lg border border-gray-200 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-850/50 px-3 py-2"
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

									<!-- Target id (autocomplete via datalist; api_key = paste id) -->
									<input
										class="flex-1 min-w-40 text-xs bg-transparent outline-none border-b border-gray-200 dark:border-gray-700 focus:border-gray-400 dark:focus:border-gray-500 py-0.5"
										list={'token-caps-' + cap.target_type + '-options'}
										placeholder={cap.target_type === 'api_key'
											? 'Paste API key id'
											: 'Select or paste a ' + cap.target_type + ' id'}
										bind:value={cap.target_id}
										on:change={scheduleSave}
										aria-label={$i18n.t('Target id')}
									/>

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
										on:click={() => removeCap(idx)}
										aria-label={$i18n.t('Remove cap')}
										title={$i18n.t('Remove cap')}
									>
										<svg viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
											<path
												d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
											/>
										</svg>
									</button>
								</div>
								<div class="mt-1 text-[0.6875rem] text-gray-400 dark:text-gray-600 pl-26">
									{$i18n.t(
										'Hourly (M) / Daily (M) / Weekly (M) / Monthly (M) — 1 = 1M tokens, 0 = unlimited'
									)}
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
			disabled={!loaded || saving}
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
