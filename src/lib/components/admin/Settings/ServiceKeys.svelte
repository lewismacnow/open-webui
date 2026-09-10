<script lang="ts">
	/**
	 * ServiceKeys
	 * -----------
	 * Admin "Service API Keys" page. Mints and revokes long-lived
	 * service-scoped API keys; the plaintext key is shown exactly once
	 * on creation. List/edit/revoke actions live on the same view.
	 */
	import { onMount, getContext, tick } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		getServiceKeys,
		getServiceKey,
		createServiceKey,
		updateServiceKey,
		revokeServiceKey,
		type ServiceKey as ServiceKeyType
	} from '$lib/apis/serviceKeys';
	import { searchGroups } from '$lib/apis/groups';
	import { copyToClipboard, formatDate } from '$lib/utils';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import SearchCombobox from '$lib/components/common/SearchCombobox.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	// --- Mint form state ---
	let mintGroupId: string = '';
	let mintGroupLabel: string = '';
	let mintName: string = '';
	let mintExpires: string = ''; // ISO local-time string from datetime-local
	let mintNeverExpires: boolean = true;
	let mintIpWhitelist: string = '';
	let minting: boolean = false;

	// One-shot plaintext revealed after a successful POST. Null = no
	// pending key. Stored as the literal response so the user can copy
	// it. Cleared on explicit dismiss.
	let revealedPlaintext: string | null = null;
	let revealedPrefix: string = '';

	// --- List state ---
	let keys: ServiceKeyType[] = [];
	let keysLoading: boolean = false;
	let loaded: boolean = false;
	// Epoch ms of the last successful (or attempted) list fetch. Used
	// for the "Updated X seconds ago" indicator + manual refresh.
	let lastFetched: number | null = null;

	// Saved snapshot for dirty detection. Keys are immutable from the
	// admin's perspective here — but inline edits update, then we save
	// back, then re-snapshot.
	let inlineEditingId: string | null = null;
	let inlinePatch: {
		name: string;
		expires: string;
		neverExpires: boolean;
		ipWhitelist: string;
	} = blankEdit();
	let inlineSaving: boolean = false;

	function blankEdit() {
		return { name: '', expires: '', neverExpires: true, ipWhitelist: '' };
	}

	// Local label cache for groups. We hydrate it when the user picks a
	// group in the mint form; resolution from existing keys happens
	// indirectly via the table fall-through to group_id.
	let groupLabelCache: Record<string, string> = {};

	// Confirm modal state.
	let confirmRevoke: ServiceKeyType | null = null;
	let showRevokeConfirm: boolean = false;
	let revoking: boolean = false;

	// Unix-seconds to local datetime-input string ("YYYY-MM-DDTHH:mm").
	// Browsers expect local clock; backend stores UTC unix seconds.
	function unixToLocalInput(secs: number | null): string {
		if (!secs) return '';
		const d = new Date(secs * 1000);
		if (Number.isNaN(d.getTime())) return '';
		const pad = (n: number) => `${n}`.padStart(2, '0');
		return (
			`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
			`T${pad(d.getHours())}:${pad(d.getMinutes())}`
		);
	}

	// Human-friendly "X ago" for the Last updated indicator. Caps at a
	// long-form date for sessions older than an hour so the label never
	// blows up the section header width.
	function formatLastFetched(epochMs: number): string {
		const delta = Math.max(0, Math.floor((Date.now() - epochMs) / 1000));
		if (delta < 5) return 'just now';
		if (delta < 60) return `${delta}s ago`;
		const m = Math.floor(delta / 60);
		if (m < 60) return `${m}m ago`;
		const h = Math.floor(m / 60);
		if (h < 24) return `${h}h ago`;
		return new Date(epochMs).toLocaleString();
	}

	function localInputToUnix(s: string): number | null {
		if (!s) return null;
		const t = new Date(s).getTime();
		return Number.isNaN(t) ? null : Math.floor(t / 1000);
	}

	// Status as a single string for the table badge. Computed client-side
	// because the backend may store revocation and expiry independently
	// and the admin wants to know the actual reason.
	type DerivedStatus = { label: string; tone: 'ok' | 'muted' | 'warn' };
	function deriveStatus(k: ServiceKeyType): DerivedStatus {
		const now = Math.floor(Date.now() / 1000);
		if (k.revoked_at !== null && k.revoked_at !== undefined) {
			// Whether expiry played a role is moot once revoked — but
			// per spec, distinguish "manual" vs "expired-first".
			const expiredFirst =
				k.expires_at !== null && k.expires_at !== undefined && k.expires_at <= (k.revoked_at ?? 0);
			return {
				label: expiredFirst ? $i18n.t('Revoked') + ' (expired)' : $i18n.t('Revoked'),
				tone: 'muted'
			};
		}
		if (k.expires_at && k.expires_at < now) {
			return { label: $i18n.t('Expired'), tone: 'warn' };
		}
		return { label: $i18n.t('Active'), tone: 'ok' };
	}

	function parseIpWhitelist(text: string): string[] {
		return text
			.split('\n')
			.map((l) => l.trim())
			.filter((l) => l.length > 0);
	}

	function joinIpWhitelist(list: string[] | undefined | null): string {
		return (list ?? []).join('\n');
	}

	// --- Network ops ---

	async function refreshKeys() {
		keysLoading = true;
		// 10s hard timeout — short enough that a stuck request can't
		// stall the UI; long enough to tolerate a slow first-byte
		// response. The AbortError fires the catch block; the spinner
		// hides via `finally`.
		const controller = new AbortController();
		const timeoutId = setTimeout(() => controller.abort(), 10_000);
		try {
			const res = await getServiceKeys(
				localStorage.token,
				undefined,
				undefined,
				true,
				controller.signal
			);
			// Defensive: handle missing or non-array items rather than crash.
			const items = Array.isArray((res as any)?.items) ? (res as any).items : [];
			// Newest first; ties broken by name.
			keys = [...items].sort((a, b) => {
				const ac = a?.created_at ?? 0;
				const bc = b?.created_at ?? 0;
				if (bc !== ac) return bc - ac;
				return (a?.name ?? '').localeCompare(b?.name ?? '');
			});
			lastFetched = Date.now();
		} catch (e: any) {
			console.error('Failed to load service keys:', e);
			const detail =
				e?.detail ??
				e?.message ??
				(e?.name === 'AbortError' ? 'Request timed out after 10s' : null);
			toast.error(detail ?? $i18n.t('Failed to load service keys'));
			keys = [];
		} finally {
			clearTimeout(timeoutId);
			keysLoading = false;
		}
	}

	async function resolveGroupLabel(id: string): Promise<string> {
		if (!id) return '';
		if (groupLabelCache[id]) return groupLabelCache[id];
		// Best-effort: search for a group whose id matches. Empty query
		// on /groups/search returns the first page.
		try {
			const res: any = await searchGroups(localStorage.token, '');
			const arr: any[] = Array.isArray(res) ? res : (res?.items ?? res?.groups ?? []);
			for (const g of arr) {
				if (g.id) groupLabelCache[g.id] = g.name ?? g.id;
			}
			return groupLabelCache[id] ?? '';
		} catch (e) {
			return '';
		}
	}

	async function submitMint() {
		if (minting) return;
		if (!mintGroupId) {
			toast.error($i18n.t('Pick a group first'));
			return;
		}
		if (!mintName.trim()) {
			toast.error($i18n.t('Name is required'));
			return;
		}
		const expires_at = mintNeverExpires ? null : localInputToUnix(mintExpires);

		minting = true;
		try {
			const result = await createServiceKey(localStorage.token, {
				group_id: mintGroupId,
				name: mintName.trim(),
				expires_at,
				ip_whitelist: parseIpWhitelist(mintIpWhitelist)
			});
			revealedPlaintext = result.plaintext ?? '';
			revealedPrefix = result.prefix ?? '';
			toast.success($i18n.t('Service key minted'));
			// Clear the form.
			mintName = '';
			mintExpires = '';
			mintNeverExpires = true;
			mintIpWhitelist = '';
			await refreshKeys();
		} catch (e: any) {
			console.error('Failed to mint service key:', e);
			toast.error(e?.detail ?? $i18n.t('Failed to mint service key'));
		} finally {
			minting = false;
		}
	}

	function beginEdit(k: ServiceKeyType) {
		if (k.revoked_at) return; // shouldn't be reachable (button hidden)
		inlineEditingId = k.id;
		inlinePatch = {
			name: k.name,
			expires: unixToLocalInput(k.expires_at),
			neverExpires: !k.expires_at,
			ipWhitelist: joinIpWhitelist(k.ip_whitelist)
		};
	}

	function cancelEdit() {
		inlineEditingId = null;
		inlinePatch = blankEdit();
	}

	async function saveEdit(k: ServiceKeyType) {
		if (inlineSaving) return;
		const patch: {
			name?: string;
			expires_at?: number | null;
			ip_whitelist?: string[];
		} = {};
		if (inlinePatch.name.trim() && inlinePatch.name !== k.name) {
			patch.name = inlinePatch.name.trim();
		}
		const newExpires = inlinePatch.neverExpires ? null : localInputToUnix(inlinePatch.expires);
		const oldExpires = k.expires_at ?? null;
		if (newExpires !== oldExpires) patch.expires_at = newExpires;
		patch.ip_whitelist = parseIpWhitelist(inlinePatch.ipWhitelist);

		if (Object.keys(patch).length === 0) {
			cancelEdit();
			return;
		}

		inlineSaving = true;
		try {
			const updated = await updateServiceKey(localStorage.token, k.id, patch);
			keys = keys.map((row) => (row.id === k.id ? { ...row, ...updated } : row));
			toast.success($i18n.t('Service key updated'));
			cancelEdit();
		} catch (e: any) {
			console.error('Failed to update service key:', e);
			toast.error(e?.detail ?? $i18n.t('Failed to update service key'));
		} finally {
			inlineSaving = false;
		}
	}

	async function confirmRevokeKey(k: ServiceKeyType) {
		if (revoking) return;
		revoking = true;
		try {
			await revokeServiceKey(localStorage.token, k.id);
			// Optimistic local update — merge the revoked_at field back.
			const now = Math.floor(Date.now() / 1000);
			keys = keys.map((row) =>
				row.id === k.id ? { ...row, revoked_at: row.revoked_at ?? now } : row
			);
			toast.success($i18n.t('Service key revoked'));
			confirmRevoke = null;
			showRevokeConfirm = false;
		} catch (e: any) {
			console.error('Failed to revoke service key:', e);
			toast.error(e?.detail ?? $i18n.t('Failed to revoke service key'));
		} finally {
			revoking = false;
		}
	}

	async function copyPlaintext() {
		if (!revealedPlaintext) return;
		try {
			await copyToClipboard(revealedPlaintext);
			toast.success($i18n.t('Copied to clipboard'));
		} catch (e) {
			toast.error($i18n.t('Failed to copy'));
		}
	}

	function dismissPlaintext() {
		revealedPlaintext = null;
		revealedPrefix = '';
	}

	// Hydrate group labels for visible rows on initial load so the table
	// shows names instead of raw ids.
	async function hydrateGroupLabels() {
		const ids = Array.from(new Set(keys.map((k) => k.group_id).filter(Boolean)));
		if (ids.length === 0) return;
		try {
			const res: any = await searchGroups(localStorage.token, '');
			const arr: any[] = Array.isArray(res) ? res : (res?.items ?? res?.groups ?? []);
			for (const g of arr) {
				if (g.id) groupLabelCache[g.id] = g.name ?? g.id;
			}
			// Force reactivity refresh.
			keys = keys;
		} catch (e) {
			// Non-fatal.
		}
	}

	onMount(async () => {
		// Each step is independently try/finally so a hung fetch on one
		// can't leave `loaded = false` (which keeps the spinner up). The
		// refreshKeys function itself now has a 30s AbortController
		// timeout, so this is belt-and-braces.
		try {
			await refreshKeys();
		} catch (e) {
			console.error('ServiceKeys onMount: refreshKeys threw:', e);
		} finally {
			loaded = true;
		}
		try {
			await hydrateGroupLabels();
		} catch (e) {
			console.error('ServiceKeys onMount: hydrateGroupLabels threw:', e);
		}
	});
</script>

<form class="flex h-full flex-col justify-between text-sm" on:submit|preventDefault={submitMint}>
	<div class="flex items-center gap-2 mb-4">
		<h2 class="text-sm font-medium text-gray-900 dark:text-white">
			{$i18n.t('service_keys.title')}
		</h2>
		<Tooltip
			content={$i18n.t(
				'Long-lived API keys bound to a group. Use them for outbound service traffic that needs Open WebUI access without going through a user session.'
			)}
			placement="top"
		>
			<svg
				xmlns="http://www.w3.org/2000/svg"
				viewBox="0 0 16 16"
				fill="currentColor"
				aria-hidden="true"
				class="size-3.5 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 cursor-help"
			>
				<path
					fill-rule="evenodd"
					d="M15 8A7 7 0 1 1 1 8a7 7 0 0 1 14 0Zm-7 3a1 1 0 1 1-2 0 1 1 0 0 1 2 0ZM7.75 4.25a.75.75 0 0 0-1.5 0v3.5a.75.75 0 0 0 .37.65l2 1.25a.75.75 0 1 0 .76-1.29l-1.63-1.02v-3.09Z"
					clip-rule="evenodd"
				/>
			</svg>
		</Tooltip>
	</div>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		<!-- ============ MINT SECTION ============ -->
		<AdminSettingSection title={$i18n.t('service_keys.section_mint')} first>
			<p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
				{$i18n.t(
					'Mint a new long-lived service API key scoped to a group. The plaintext key is shown exactly once after creation — copy it immediately and store it securely; we cannot redisplay it.'
				)}
			</p>

			<!-- Plaintext reveal (shown only right after a successful mint) -->
			{#if revealedPlaintext}
				<div
					role="alert"
					class="mb-4 rounded-lg border-2 border-amber-500 bg-amber-50 dark:bg-amber-950/40 dark:border-amber-400 px-4 py-3"
				>
					<div class="flex items-start gap-2 mb-2">
						<svg
							xmlns="http://www.w3.org/2000/svg"
							fill="none"
							viewBox="0 0 24 24"
							stroke-width="2"
							stroke="currentColor"
							aria-hidden="true"
							class="size-5 text-amber-700 dark:text-amber-300 shrink-0 mt-0.5"
						>
							<path
								stroke-linecap="round"
								stroke-linejoin="round"
								d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"
							/>
						</svg>
						<div class="flex-1">
							<div class="text-xs font-medium text-amber-800 dark:text-amber-200">
								{$i18n.t('Copy this key now. It will not be shown again.')}
							</div>
						</div>
					</div>
					<div class="flex items-center gap-2 mb-2">
						<input
							class="flex-1 min-w-0 font-mono text-xs px-3 py-2 rounded border border-amber-300 dark:border-amber-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-white"
							readonly
							value={revealedPlaintext}
							on:focus={(e) => (e.currentTarget as HTMLInputElement).select()}
						/>
						<button
							type="button"
							class="px-3 py-2 text-xs font-medium bg-amber-600 hover:bg-amber-700 text-white rounded transition"
							on:click={copyPlaintext}
						>
							{$i18n.t('Copy')}
						</button>
					</div>
					{#if revealedPrefix}
						<div class="text-[0.6875rem] text-amber-700/80 dark:text-amber-300/80 font-mono">
							{$i18n.t('Prefix')}: {revealedPrefix}
						</div>
					{/if}
					<button
						type="button"
						class="mt-2 text-[0.6875rem] text-amber-800/80 dark:text-amber-200/80 hover:underline"
						on:click={dismissPlaintext}
					>
						{$i18n.t('Dismiss')}
					</button>
				</div>
			{/if}

			<!-- Mint form -->
			<div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
				<div>
					<label class="text-xs text-gray-600 dark:text-gray-400" for="service-keys-group">
						{$i18n.t('service_keys.group')}
					</label>
					<div class="mt-1">
						<SearchCombobox
							bind:value={mintGroupId}
							placeholder={$i18n.t('service_keys.group_placeholder')}
							disabled={minting}
							endpoint={async (q: string) => {
								const res: any = await searchGroups(localStorage.token, q);
								const arr: any[] = Array.isArray(res) ? res : (res?.items ?? res?.groups ?? []);
								const out = arr.map((g: any) => ({
									id: g.id,
									label: g.name ?? g.id
								}));
								// Update label cache for table hydration.
								for (const o of out) groupLabelCache[o.id] = o.label;
								// Auto-set label for the currently-bound value.
								if (mintGroupId) {
									const m = out.find((o) => o.id === mintGroupId);
									if (m) mintGroupLabel = m.label;
								}
								return out;
							}}
							onChange={(id, label) => {
								mintGroupId = id;
								mintGroupLabel = label;
							}}
						/>
					</div>
				</div>

				<div>
					<label class="text-xs text-gray-600 dark:text-gray-400" for="service-keys-name">
						{$i18n.t('service_keys.name')} <span class="text-red-500">*</span>
					</label>
					<input
						id="service-keys-name"
						class="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 px-3 py-1.5 text-xs text-gray-900 dark:text-white outline-hidden focus:border-blue-400 dark:focus:border-blue-500 transition disabled:opacity-50"
						type="text"
						bind:value={mintName}
						placeholder={$i18n.t('e.g. reporting-bot-prod')}
						disabled={minting}
					/>
				</div>

				<div class="md:col-span-2">
					<div class="flex items-center gap-2 mb-1">
						<label class="text-xs text-gray-600 dark:text-gray-400" for="service-keys-expiry">
							{$i18n.t('service_keys.expires_at')}
						</label>
						<span class="text-[0.6875rem] text-gray-400 dark:text-gray-500">
							({$i18n.t('service_keys.never_expires')}
							<Switch state={mintNeverExpires} on:change={(e) => (mintNeverExpires = e.detail)} />)
						</span>
					</div>
					<input
						id="service-keys-expiry"
						class="w-full max-w-xs rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 px-3 py-1.5 text-xs text-gray-900 dark:text-white outline-hidden focus:border-blue-400 dark:focus:border-blue-500 transition disabled:opacity-50"
						type="datetime-local"
						bind:value={mintExpires}
						disabled={mintNeverExpires || minting}
					/>
				</div>

				<div class="md:col-span-2">
					<label class="text-xs text-gray-600 dark:text-gray-400" for="service-keys-ip">
						{$i18n.t('service_keys.ip_whitelist')}
					</label>
					<textarea
						id="service-keys-ip"
						class="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900 px-3 py-2 text-xs text-gray-900 dark:text-white outline-hidden focus:border-blue-400 dark:focus:border-blue-500 transition resize-y font-mono disabled:opacity-50"
						rows="4"
						bind:value={mintIpWhitelist}
						placeholder={$i18n.t(
							'One CIDR per line (e.g. 10.0.0.0/8). Leave blank to allow any IP.'
						)}
						disabled={minting}
					/>
				</div>
			</div>

			<button
				type="submit"
				class="px-3.5 py-1.5 text-sm font-medium bg-black dark:bg-white text-white dark:text-black rounded-lg transition disabled:opacity-50"
				disabled={minting || !loaded}
			>
				{minting ? $i18n.t('Minting...') : $i18n.t('service_keys.mint')}
			</button>
		</AdminSettingSection>

		<!-- ============ LIST SECTION ============ -->
		<AdminSettingSection title={$i18n.t('service_keys.section_list')}>
			<div class="flex items-start justify-between gap-4 mb-4">
				<p class="text-sm text-gray-500 dark:text-gray-400 flex-1">
					{$i18n.t(
						'Existing service keys. Revoked and expired keys are retained for audit visibility.'
					)}
				</p>
				<div class="flex items-center gap-2 shrink-0">
					{#if lastFetched}
						<span class="text-[0.6875rem] text-gray-400 dark:text-gray-500">
							{$i18n.t('service_keys.updated_at', {
								at: formatLastFetched(lastFetched)
							})}
						</span>
					{/if}
					<button
						type="button"
						class="px-2.5 py-1 text-xs rounded bg-gray-100 dark:bg-gray-850 hover:bg-gray-200 dark:hover:bg-gray-800 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
						on:click={refreshKeys}
						disabled={keysLoading}
						aria-label={$i18n.t('Refresh')}
					>
						<span class:animate-spin={keysLoading} aria-hidden="true">↻</span>
						<span>{keysLoading ? $i18n.t('Refreshing...') : $i18n.t('Refresh')}</span>
					</button>
				</div>
			</div>

			{#if !loaded || keysLoading}
				<div class="flex justify-center py-6"><Spinner /></div>
			{:else if keys.length === 0}
				<div
					class="text-xs text-gray-400 dark:text-gray-500 py-6 text-center border border-dashed border-gray-300 dark:border-gray-700 rounded-lg"
				>
					{$i18n.t(
						'No service keys yet. Mint one above to get a long-lived API key bound to a group.'
					)}
				</div>
			{:else}
				<div class="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-800">
					<table class="w-full text-xs">
						<thead class="bg-gray-50 dark:bg-gray-850 text-gray-500 dark:text-gray-400">
							<tr>
								<th class="px-3 py-2 text-left font-medium">
									{$i18n.t('service_keys.col_group')}
								</th>
								<th class="px-3 py-2 text-left font-medium">
									{$i18n.t('service_keys.col_name')}
								</th>
								<th class="px-3 py-2 text-left font-medium">
									{$i18n.t('service_keys.col_prefix')}
								</th>
								<th class="px-3 py-2 text-left font-medium">
									{$i18n.t('service_keys.col_created')}
								</th>
								<th class="px-3 py-2 text-left font-medium">
									{$i18n.t('service_keys.col_expires')}
								</th>
								<th class="px-3 py-2 text-left font-medium">
									{$i18n.t('service_keys.col_last_used')}
								</th>
								<th class="px-3 py-2 text-left font-medium">
									{$i18n.t('service_keys.col_ip_whitelist')}
								</th>
								<th class="px-3 py-2 text-left font-medium">
									{$i18n.t('service_keys.col_status')}
								</th>
								<th class="px-3 py-2 text-right font-medium">
									{$i18n.t('service_keys.col_actions')}
								</th>
							</tr>
						</thead>
						<tbody class="divide-y divide-gray-100 dark:divide-gray-850">
							{#each keys as k (k.id)}
								{@const status = deriveStatus(k)}
								{@const isEditing = inlineEditingId === k.id}
								<tr class={isEditing ? 'bg-blue-50/30 dark:bg-blue-950/10' : ''}>
									{#if isEditing}
										<td colspan="9" class="px-3 py-3">
											<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
												<div>
													<label class="text-[0.6875rem] text-gray-500">
														{$i18n.t('service_keys.name')}
													</label>
													<input
														class="mt-0.5 w-full rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-xs"
														type="text"
														bind:value={inlinePatch.name}
														disabled={inlineSaving}
													/>
												</div>
												<div>
													<div class="flex items-center gap-2">
														<label class="text-[0.6875rem] text-gray-500">
															{$i18n.t('service_keys.expires_at')}
														</label>
														<span class="text-[0.6875rem] text-gray-400">
															({$i18n.t('service_keys.never_expires')}
															<Switch
																state={inlinePatch.neverExpires}
																on:change={(e) => (inlinePatch.neverExpires = e.detail)}
															/>)
														</span>
													</div>
													<input
														class="mt-0.5 w-full max-w-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-xs"
														type="datetime-local"
														bind:value={inlinePatch.expires}
														disabled={inlinePatch.neverExpires || inlineSaving}
													/>
												</div>
												<div class="md:col-span-2">
													<label class="text-[0.6875rem] text-gray-500">
														{$i18n.t('service_keys.ip_whitelist')}
													</label>
													<textarea
														class="mt-0.5 w-full rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-2 py-1 text-xs font-mono"
														rows="3"
														bind:value={inlinePatch.ipWhitelist}
														disabled={inlineSaving}
													/>
												</div>
												<div class="md:col-span-2 flex justify-end gap-2">
													<button
														type="button"
														class="px-2.5 py-1 text-xs rounded bg-gray-100 dark:bg-gray-850 hover:bg-gray-200 dark:hover:bg-gray-800"
														on:click={cancelEdit}
														disabled={inlineSaving}
													>
														{$i18n.t('Cancel')}
													</button>
													<button
														type="button"
														class="px-2.5 py-1 text-xs rounded bg-black dark:bg-white text-white dark:text-black disabled:opacity-50"
														on:click={() => saveEdit(k)}
														disabled={inlineSaving}
													>
														{inlineSaving ? $i18n.t('Saving...') : $i18n.t('Save')}
													</button>
												</div>
											</div>
										</td>
									{:else}
										<td class="px-3 py-2 truncate max-w-32">
											{groupLabelCache[k.group_id] ?? k.group_id}
										</td>
										<td class="px-3 py-2 font-medium">{k.name}</td>
										<td
											class="px-3 py-2 font-mono text-[0.6875rem] text-gray-700 dark:text-gray-300"
										>
											{k.prefix || '----'}
										</td>
										<td class="px-3 py-2 text-gray-500 dark:text-gray-400">
											{formatDate(k.created_at)}
										</td>
										<td class="px-3 py-2 text-gray-500 dark:text-gray-400">
											{k.expires_at ? formatDate(k.expires_at) : '—'}
										</td>
										<td class="px-3 py-2 text-gray-500 dark:text-gray-400">
											{k.last_used_at ? formatDate(k.last_used_at) : '—'}
										</td>
										<td class="px-3 py-2">
											{#if k.ip_whitelist && k.ip_whitelist.length > 0}
												<Tooltip content={k.ip_whitelist.join('\n')}>
													<span
														class="inline-flex items-center px-1.5 py-0.5 rounded text-[0.6875rem] bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 cursor-help"
													>
														{$i18n.t('service_keys.ip_count', { n: k.ip_whitelist.length })}
													</span>
												</Tooltip>
											{:else}
												<span class="text-[0.6875rem] text-gray-300 dark:text-gray-600">
													{$i18n.t('any')}
												</span>
											{/if}
										</td>
										<td class="px-3 py-2">
											<span
												class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[0.6875rem] {status.tone ===
												'ok'
													? 'bg-green-100 dark:bg-green-950/40 text-green-700 dark:text-green-300'
													: status.tone === 'warn'
														? 'bg-amber-100 dark:bg-amber-950/40 text-amber-700 dark:text-amber-300'
														: 'bg-gray-200 dark:bg-gray-800 text-gray-600 dark:text-gray-400'}"
											>
												{status.label}
											</span>
										</td>
										<td class="px-3 py-2 text-right">
											{#if !k.revoked_at}
												<div class="flex justify-end gap-1">
													<button
														type="button"
														class="text-xs text-blue-600 dark:text-blue-400 hover:underline disabled:opacity-50"
														on:click={() => beginEdit(k)}
													>
														{$i18n.t('Edit')}
													</button>
													<button
														type="button"
														class="text-xs text-red-600 dark:text-red-400 hover:underline disabled:opacity-50"
														on:click={() => {
															confirmRevoke = k;
															showRevokeConfirm = true;
														}}
													>
														{$i18n.t('Revoke')}
													</button>
												</div>
											{/if}
										</td>
									{/if}
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</AdminSettingSection>
	</div>
</form>

<ConfirmDialog
	bind:show={showRevokeConfirm}
	title={confirmRevoke ? $i18n.t('service_keys.revoke_title', { name: confirmRevoke.name }) : ''}
	message={confirmRevoke
		? $i18n.t('service_keys.revoke_message', {
				name: confirmRevoke.name,
				prefix: confirmRevoke.prefix
			})
		: ''}
	confirmLabel={$i18n.t('Revoke')}
	onConfirm={() => confirmRevoke && confirmRevokeKey(confirmRevoke)}
	on:cancel={() => (confirmRevoke = null)}
/>
