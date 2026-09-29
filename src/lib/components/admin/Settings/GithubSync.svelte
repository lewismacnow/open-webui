<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		pruneFailedFiles,
		getGithubCredentials,
		createGithubCredential,
		deleteGithubCredential,
		getGithubSyncSources,
		createGithubSyncSource,
		updateGithubSyncSource,
		deleteGithubSyncSource,
		syncGithubSyncSourceNow,
		type GithubCredential,
		type GithubSyncSource
	} from '$lib/apis/knowledgeGithub';
	import { getKnowledgeBases } from '$lib/apis/knowledge';

	import Switch from '$lib/components/common/Switch.svelte';
	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	let credentials: GithubCredential[] = [];
	let sources: GithubSyncSource[] = [];
	let knowledgeBases: any[] = [];
	let loaded = false;
	let busy = false;

	// Credential form
	let showCredForm = false;
	let credName = '';
	let credToken = '';
	let credNote = '';

	// Source form
	let showSourceForm = false;
	let srcKnowledgeId = '';
	let srcRepoUrl = '';
	let srcBranch = 'main';
	let srcDirectory = '';
	let srcCredentialId = '';
	let srcIntervalHours = 24;
	let srcRemoveDeleted = true;

	function parseRepoUrl(url: string): { owner: string; repo: string } | null {
		const m = url.trim().match(/(?:github\.com[\/:])([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)/);
		if (!m) return null;
		return { owner: m[1], repo: m[2].replace(/\.git$/, '') };
	}

	async function refresh() {
		const token = localStorage.token;
		try {
			[credentials, sources] = await Promise.all([
				getGithubCredentials(token),
				getGithubSyncSources(token)
			]);
		} catch (e) {
			console.error(e);
		}
	}

	async function addCredential() {
		if (!credName.trim() || !credToken.trim()) {
			toast.error($i18n.t('Name and token are required'));
			return;
		}
		busy = true;
		try {
			await createGithubCredential(localStorage.token, {
				name: credName.trim(),
				token: credToken.trim(),
				note: credNote.trim() || null
			});
			toast.success($i18n.t('GitHub token saved (encrypted at rest)'));
			credName = credToken = credNote = '';
			showCredForm = false;
			await refresh();
		} catch (e) {
			toast.error(String(e));
		} finally {
			busy = false;
		}
	}

	async function removeCredential(id: string) {
		try {
			await deleteGithubCredential(localStorage.token, id);
			toast.success($i18n.t('Token deleted'));
			await refresh();
		} catch (e) {
			toast.error(String(e));
		}
	}

	async function addSource() {
		const parsed = parseRepoUrl(srcRepoUrl);
		if (!srcKnowledgeId) {
			toast.error($i18n.t('Select a knowledge base'));
			return;
		}
		if (!parsed) {
			toast.error($i18n.t('Enter a valid GitHub repository URL'));
			return;
		}
		busy = true;
		try {
			await createGithubSyncSource(localStorage.token, {
				knowledge_id: srcKnowledgeId,
				repo_owner: parsed.owner,
				repo_name: parsed.repo,
				branch: srcBranch.trim() || 'main',
				directory_path: srcDirectory.trim(),
				credential_id: srcCredentialId || null,
				remove_deleted: srcRemoveDeleted,
				interval_seconds: srcIntervalHours > 0 ? srcIntervalHours * 3600 : null
			});
			toast.success($i18n.t('Source created — run "Sync now" to import'));
			srcRepoUrl = srcBranch = 'main';
			srcDirectory = '';
			showSourceForm = false;
			await refresh();
		} catch (e) {
			toast.error(String(e));
		} finally {
			busy = false;
		}
	}

	async function cleanFailed(source: GithubSyncSource) {
		if (
			!confirm(
				$i18n.t('Remove all FAILED files (empty or unembeddable content) from this knowledge base?')
			)
		)
			return;
		try {
			const res = await pruneFailedFiles(localStorage.token, source.knowledge_id);
			toast.success($i18n.t('Removed {n} failed file(s)', { n: res.removed }));
			if (res.details?.length) console.info('prune details:', res.details);
			await refresh();
		} catch (e) {
			toast.error(String(e));
		}
	}

	async function syncNow(source: GithubSyncSource) {
		toast.info($i18n.t('Syncing {repo}…', { repo: source.repo_full_name }));
		try {
			const result = await syncGithubSyncSourceNow(localStorage.token, source.id);
			const r = result || {};
			toast.success(
				$i18n.t('Sync done: +{added} added, ~{updated} updated, −{removed} removed', {
					added: r.added ?? 0,
					updated: r.updated ?? 0,
					removed: r.removed ?? 0
				})
			);
			await refresh();
		} catch (e) {
			toast.error(String(e));
		}
	}

	async function toggleEnabled(source: GithubSyncSource, value: boolean) {
		try {
			await updateGithubSyncSource(localStorage.token, source.id, { enabled: value });
			await refresh();
		} catch (e) {
			toast.error(String(e));
		}
	}

	async function removeSource(source: GithubSyncSource) {
		if (!confirm($i18n.t('Delete this source? Synced files stay unless you check removal.')))
			return;
		const removeFiles = confirm(
			$i18n.t('Also DELETE the files this source synced into the knowledge base?')
		);
		try {
			await deleteGithubSyncSource(localStorage.token, source.id, removeFiles);
			toast.success($i18n.t('Source deleted'));
			await refresh();
		} catch (e) {
			toast.error(String(e));
		}
	}

	function formatInterval(seconds: number | null): string {
		if (!seconds) return $i18n.t('Manual only');
		if (seconds % 86400 === 0) return $i18n.t('Every {n}d', { n: seconds / 86400 });
		if (seconds % 3600 === 0) return $i18n.t('Every {n}h', { n: seconds / 3600 });
		return $i18n.t('Every {n}m', { n: Math.round(seconds / 60) });
	}

	onMount(async () => {
		await refresh();
		try {
			const kbResponse = (await getKnowledgeBases(localStorage.token)) ?? [];
			// /api/v1/knowledge/ returns a paginated envelope {items, total};
			// fall back to a bare array for forward-compat.
			knowledgeBases = Array.isArray(kbResponse) ? kbResponse : (kbResponse?.items ?? []);
		} catch (e) {
			console.error(e);
		}
		loaded = true;
	});
</script>

<AdminSettingSection title={$i18n.t('GitHub Tokens')}>
	<p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
		{$i18n.t(
			'Tokens raise the GitHub rate limit (60 → 5,000 requests/hour) and unlock private repos. They are encrypted at rest and never displayed again — GitHub tokens can be re-created at any time from GitHub settings.'
		)}
	</p>
	<div
		class="text-xs bg-gray-50 dark:bg-gray-950/40 border border-gray-200 dark:border-gray-800/50 rounded-lg p-3 mb-4"
	>
		<div class="font-medium mb-1">{$i18n.t('Recommended token scopes')}</div>
		<div class="text-gray-500 dark:text-gray-400">
			{$i18n.t(
				'Fine-grained PAT: repository access → Only select repositories → Permissions: Contents → Read-only. No other scopes are needed — sync is strictly read-only.'
			)}
		</div>
	</div>

	{#if credentials.length}
		<div class="space-y-2 mb-4">
			{#each credentials as cred (cred.id)}
				<div
					class="flex items-center justify-between gap-3 p-3 rounded-xl border border-gray-100 dark:border-gray-850"
				>
					<div class="flex-1 min-w-0">
						<div class="text-sm font-medium truncate">
							{cred.name}
							<span class="ml-2 text-xs text-gray-400 font-mono">…{cred.token_suffix}</span>
						</div>
						<p class="text-xs text-gray-400">
							{cred.source_count} source(s)
							{#if cred.last_used_at}
								· {$i18n.t('last used')} {new Date(cred.last_used_at * 1000).toLocaleDateString()}
							{/if}
						</p>
					</div>
					<button
						type="button"
						class="text-xs px-2.5 py-1.5 rounded-lg text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30"
						on:click={() => removeCredential(cred.id)}
					>
						{$i18n.t('Delete')}
					</button>
				</div>
			{/each}
		</div>
	{/if}

	{#if showCredForm}
		<div class="space-y-3 p-3 rounded-xl border border-gray-200 dark:border-gray-800/50 mb-4">
			<input
				class="w-full text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5"
				placeholder={$i18n.t('Token name (e.g. "ServiceNow docs bot")')}
				bind:value={credName}
			/>
			<input
				class="w-full text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5 font-mono"
				placeholder="github_pat_… / ghp_…"
				bind:value={credToken}
				type="password"
			/>
			<input
				class="w-full text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5"
				placeholder={$i18n.t('Note (optional)')}
				bind:value={credNote}
			/>
			<div class="flex gap-2">
				<button
					type="button"
					class="px-3.5 py-1.5 text-sm font-medium bg-black dark:bg-white text-white dark:text-black rounded-lg"
					disabled={busy}
					on:click={addCredential}
				>
					{$i18n.t('Save token')}
				</button>
				<button
					type="button"
					class="text-sm px-3 py-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850"
					on:click={() => (showCredForm = false)}
				>
					{$i18n.t('Cancel')}
				</button>
			</div>
		</div>
	{:else}
		<button
			type="button"
			class="text-sm px-3.5 py-1.5 rounded-lg border border-gray-200 dark:border-gray-850 hover:bg-gray-50 dark:hover:bg-gray-850"
			on:click={() => (showCredForm = true)}
		>
			+ {$i18n.t('Add token')}
		</button>
	{/if}
</AdminSettingSection>

<AdminSettingSection title={$i18n.t('Sync Sources')}>
	<p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
		{$i18n.t(
			'Each source mirrors one repository directory into a knowledge base. Content is fetched read-only; files pass extension, size, content-type and content scans before embedding. Changed files re-embed automatically on the next scheduled sync.'
		)}
	</p>

	{#if sources.length}
		<div class="space-y-2 mb-4">
			{#each sources as source (source.id)}
				<div class="p-3 rounded-xl border border-gray-100 dark:border-gray-850">
					<div class="flex items-center justify-between gap-3">
						<div class="flex-1 min-w-0">
							<div class="text-sm font-medium truncate">
								{source.repo_full_name}
								<span class="text-xs text-gray-400 font-mono">@{source.branch}</span>
							</div>
							<p class="text-xs text-gray-400 truncate">
								{source.knowledge_name || source.knowledge_id.slice(0, 8)} ·
								{source.directory_path || '/'}
								· {formatInterval(source.interval_seconds)}
								{#if source.last_sync_status === 'error'}
									<span class="text-red-500">· {$i18n.t('last sync failed')}</span>
								{:else if source.last_sync_at}
									<span class="text-green-500"
										>· {new Date(source.last_sync_at * 1000).toLocaleDateString()}</span
									>
								{/if}
							</p>
						</div>
						<div class="flex items-center gap-1.5 shrink-0">
							<button
								type="button"
								class="text-xs px-2.5 py-1.5 rounded-lg border border-gray-200 dark:border-gray-850 hover:bg-gray-50 dark:hover:bg-gray-850"
								on:click={() => syncNow(source)}
							>
								{$i18n.t('Sync now')}
							</button>
							<button
								type="button"
								title={$i18n.t('Remove failed files (empty/unembeddable) from this knowledge base')}
								class="text-xs px-2.5 py-1.5 rounded-lg border border-gray-200 dark:border-gray-850 hover:bg-amber-50 dark:hover:bg-amber-950/20"
								on:click={() => cleanFailed(source)}
							>
								{$i18n.t('Clean failed')}
							</button>
							<button
								type="button"
								class="text-xs px-2 py-1.5 rounded-lg text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30"
								on:click={() => removeSource(source)}
							>
								{$i18n.t('Delete')}
							</button>
							<Switch state={source.enabled} on:change={(e) => toggleEnabled(source, e.detail)} />
						</div>
					</div>
					{#if source.last_sync_result}
						<div class="mt-2 text-xs text-gray-400">
							{JSON.stringify(
								Object.fromEntries(
									Object.entries(source.last_sync_result).filter(
										([k, v]) => k !== 'errors' && typeof v === 'number'
									)
								)
							)}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}

	{#if showSourceForm}
		<div class="space-y-3 p-3 rounded-xl border border-gray-200 dark:border-gray-800/50">
			<select
				class="w-full text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5"
				bind:value={srcKnowledgeId}
			>
				<option value="">{$i18n.t('Select knowledge base…')}</option>
				{#each knowledgeBases as kb (kb.id)}
					<option value={kb.id}>{kb.name}</option>
				{/each}
			</select>
			<input
				class="w-full text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5"
				placeholder="https://github.com/ServiceNow/ServiceNowDocs"
				bind:value={srcRepoUrl}
			/>
			<div class="grid grid-cols-2 gap-3">
				<input
					class="text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5"
					placeholder={$i18n.t('Branch (e.g. brazil)')}
					bind:value={srcBranch}
				/>
				<input
					class="text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5"
					placeholder={$i18n.t('Directory (e.g. markdown)')}
					bind:value={srcDirectory}
				/>
			</div>
			<div class="grid grid-cols-2 gap-3">
				<select
					class="text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5"
					bind:value={srcCredentialId}
				>
					<option value="">{$i18n.t('No token (public repos, rate-limited)')}</option>
					{#each credentials as cred (cred.id)}
						<option value={cred.id}>{cred.name} (…{cred.token_suffix})</option>
					{/each}
				</select>
				<input
					type="number"
					min="0"
					class="text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5"
					placeholder={$i18n.t('Sync every N hours (0 = manual)')}
					bind:value={srcIntervalHours}
				/>
			<input
				class="text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5 col-span-2"
				placeholder={$i18n.t('Allowed extensions (e.g. md, txt, py, json) — empty uses admin default')}
				bind:value={srcAllowedExtensions}
			/>
			</div>
			<AdminSettingField
				label={$i18n.t('Remove files deleted from the repository')}
				description={$i18n.t(
					'On each sync, files removed from the repo are also removed from the knowledge base.'
				)}
			>
				<Switch bind:state={srcRemoveDeleted} />
			</AdminSettingField>
			<div class="flex gap-2">
				<button
					type="button"
					class="px-3.5 py-1.5 text-sm font-medium bg-black dark:bg-white text-white dark:text-black rounded-lg"
					disabled={busy}
					on:click={addSource}
				>
					{$i18n.t('Create source')}
				</button>
				<button
					type="button"
					class="text-sm px-3 py-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850"
					on:click={() => (showSourceForm = false)}
				>
					{$i18n.t('Cancel')}
				</button>
			</div>
		</div>
	{:else}
		<button
			type="button"
			class="text-sm px-3.5 py-1.5 rounded-lg border border-gray-200 dark:border-gray-850 hover:bg-gray-50 dark:hover:bg-gray-850"
			on:click={() => (showSourceForm = true)}
		>
			+ {$i18n.t('Add source')}
		</button>
	{/if}
</AdminSettingSection>
