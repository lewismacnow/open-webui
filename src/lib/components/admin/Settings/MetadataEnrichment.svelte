<script lang="ts">
	import { onMount, onDestroy, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		cancelMetadataScan,
		startMetadataScan,
		getMetadataScans,
		getEnrichmentFiles,
		ignoreEnrichmentFile,
		unignoreEnrichmentFile,
		type MetadataScan,
		type KnowledgeEnrichmentFile
	} from '$lib/apis/metadataSuggestions';
	import { getKnowledgeBases } from '$lib/apis/knowledge';
	import { getAllModels } from '$lib/apis/models';
	import { getModels } from '$lib/apis';
	import { getKnowledgeFiles } from '$lib/apis/knowledge';
	import { formatFileSize } from '$lib/utils';

	import Switch from '$lib/components/common/Switch.svelte';
	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	const ATTRIBUTE_OPTIONS = [
		{ id: 'title', label: 'Title' },
		{ id: 'description', label: 'Description' },
		{ id: 'summary', label: 'Summary' },
		{ id: 'tags', label: 'Tags' },
		{ id: 'keywords', label: 'Keywords' },
		{ id: 'category', label: 'Category' },
		{ id: 'doc_type', label: 'Doc type' },
		{ id: 'audience', label: 'Audience' }
	];

	const IGNORED_REASON_LABELS: Record<string, string> = {
		low_value: 'Low value',
		empty: 'Empty',
		links_only: 'Links only',
		manual: 'Manual'
	};

	let knowledgeBases: any[] = [];
	let baseModels: any[] = [];
	let workspaceModels: any[] = [];
	let fileDropdownOpen: boolean = false;
	// Single-file mode picker state.
	let kbFiles: any[] = [];
	let kbFileFilter: string = '';
	let selectedKbFileId: string = '';
	let kbFilesLoading: boolean = false;
	let kbFilesFetchToken = 0;
	let prevKb = '';
	let loaded = false;
	let busy = false;

	// Scan form
	let scanKnowledgeId = '';
	let scanModelId = '';
	let scanMode: 'all' | 'missing' | 'attributes' | 'file' = 'all';
	let selectedAttributes: string[] = ['title', 'description', 'summary', 'tags'];
	let scanFileId = '';
	let maxParallel = 1;
	let acknowledgedCost = false;
	// Scan behaviour flags (new automatic mode).
	let improveContent = true;
	let updateTitles = true;
	let ignoreLowValue = true;
	let forceReenrich = false;
	let includeIgnored = false;

	// Progress + results
	let latestScan: MetadataScan | null = null;
	let pollTimer: any = null;

	// Ignored files section state.
	let showIgnoredOnly = false;
	let enrichmentFiles: KnowledgeEnrichmentFile[] = [];
	let enrichmentFilesLoading = false;
	let ignoreBusy: Record<string, boolean> = {};

	// Friendly labels for common owned_by values; unknown providers fall
	// back to titlecase so every group still reads cleanly.
	const PROVIDER_LABELS: Record<string, string> = {
		openai: 'OpenAI',
		ollama: 'Ollama',
		anthropic: 'Anthropic',
		azure: 'Azure OpenAI',
		google: 'Google',
		'google-vertex': 'Vertex AI',
		groq: 'Groq',
		mistral: 'Mistral',
		cohere: 'Cohere',
		openrouter: 'OpenRouter',
		together: 'Together',
		deepseek: 'DeepSeek',
		xai: 'xAI',
		'z-ai': 'Z.AI',
		llama_cpp: 'Llama C++',
		lm_studio: 'LM Studio',
		vllm: 'vLLM',
		arena: 'Arena'
	};

	function providerLabel(ownedBy: string | undefined): string {
		const key = (ownedBy || 'unknown').toLowerCase();
		if (PROVIDER_LABELS[key]) return PROVIDER_LABELS[key];
		return (ownedBy || 'unknown').replace(/\b\w/g, (c) => c.toUpperCase());
	}

	const byName = (a: any, b: any) => (a?.name ?? '').localeCompare(b?.name ?? '');

	// Group 1: wrapper (workspace) models, alphabetical.
	$: wrapperModels = [...workspaceModels].sort(byName);
	// Mirror picker selection into the scan payload.
	$: if (scanMode === 'file') scanFileId = selectedKbFileId;
	$: selectedFileName = kbFiles.find((f) => f?.id === selectedKbFileId)?.filename ?? '';
	$: filteredKbFiles = (() => {
		const q = kbFileFilter.trim().toLowerCase();
		if (!q) return kbFiles;
		return kbFiles.filter(
			(f) =>
				(f?.filename ?? '').toLowerCase().includes(q) || (f?.id ?? '').toLowerCase().includes(q)
		);
	})();
	// Groups 2..N: base models grouped by owned_by, groups + items alphabetical.
	$: baseGroups = Object.entries(
		baseModels.reduce((acc: Record<string, any[]>, m) => {
			const key = (m?.owned_by || 'unknown').toLowerCase();
			(acc[key] ??= []).push(m);
			return acc;
		}, {})
	)
		.map(([key, ms]) => [providerLabel(key), [...ms].sort(byName)] as [string, any[]])
		.sort(([a], [b]) => a.localeCompare(b));

	const modeLabels: Record<string, string> = {
		all: 'Entire knowledge base',
		missing: 'Entire KB — only files with missing metadata',
		attributes: 'Entire KB — specific attributes only',
		file: 'Single file'
	};

	async function refresh() {
		const token = localStorage.token;
		try {
			const scans = await getMetadataScans(token, scanKnowledgeId || null, 5);
			latestScan = scans[0] ?? null;
		} catch (e) {
			console.error(e);
		}
	}

	async function refreshIgnoredFiles() {
		if (!scanKnowledgeId) {
			enrichmentFiles = [];
			return;
		}
		enrichmentFilesLoading = true;
		try {
			enrichmentFiles = await getEnrichmentFiles(
				localStorage.token,
				scanKnowledgeId,
				showIgnoredOnly
			);
		} catch (e) {
			console.error(e);
			toast.error(String(e));
		} finally {
			enrichmentFilesLoading = false;
		}
	}

	function startPolling() {
		stopPolling();
		pollTimer = setInterval(async () => {
			await refresh();
			if (latestScan && latestScan.status !== 'running') {
				stopPolling();
				if (latestScan.status === 'completed') {
					toast.success(
						$i18n.t('Scan complete: {p} changes from {n} files', {
							p: latestScan.proposals_created,
							n: latestScan.processed_files
						})
					);
					// Refresh ignored-files list after a successful scan.
					refreshIgnoredFiles();
				}
			}
		}, 3000);
	}

	function stopPolling() {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	}

	async function runTest() {
		if (!scanKnowledgeId || !scanModelId) {
			toast.error($i18n.t('Select a knowledge base and a model'));
			return;
		}
		if (!acknowledgedCost) {
			toast.error($i18n.t('Acknowledge the LLM cost warning first'));
			return;
		}
		busy = true;
		try {
			latestScan = await startMetadataScan(localStorage.token, {
				knowledge_id: scanKnowledgeId,
				model_id: scanModelId,
				mode: 'all',
				test_run: true,
				max_parallel: 1,
				improve_content: improveContent,
				update_titles: updateTitles,
				ignore_low_value: ignoreLowValue,
				include_ignored: includeIgnored,
				force: forceReenrich
			});
			toast.info($i18n.t('Test run started - 1 file, preview only'));
			startPolling();
		} catch (e) {
			toast.error(String(e));
		} finally {
			busy = false;
		}
	}

	async function runScan() {
		if (!scanKnowledgeId || !scanModelId) {
			toast.error($i18n.t('Select a knowledge base and a model'));
			return;
		}
		if (!acknowledgedCost) {
			toast.error($i18n.t('Acknowledge the LLM cost warning first'));
			return;
		}
		if (scanMode === 'file' && !scanFileId.trim()) {
			toast.error($i18n.t('Enter a file ID for single-file mode'));
			return;
		}
		if (scanMode === 'attributes' && selectedAttributes.length === 0) {
			toast.error($i18n.t('Select at least one attribute'));
			return;
		}
		busy = true;
		try {
			latestScan = await startMetadataScan(localStorage.token, {
				knowledge_id: scanKnowledgeId,
				model_id: scanModelId,
				mode: scanMode,
				attributes: scanMode === 'attributes' ? selectedAttributes : null,
				file_id: scanMode === 'file' ? scanFileId.trim() : null,
				max_parallel: maxParallel,
				improve_content: improveContent,
				update_titles: updateTitles,
				ignore_low_value: ignoreLowValue,
				include_ignored: includeIgnored,
				force: forceReenrich
			});
			toast.info($i18n.t('Enrichment started'));
			startPolling();
		} catch (e) {
			toast.error(String(e));
		} finally {
			busy = false;
		}
	}

	async function cancelScan() {
		if (!latestScan || latestScan.status !== 'running') return;
		try {
			await cancelMetadataScan(localStorage.token, latestScan.id);
			toast.info($i18n.t('Cancellation requested - in-flight file finishes, then stops'));
			await refresh();
		} catch (e) {
			toast.error(String(e));
		}
	}

	async function setFileIgnored(file: KnowledgeEnrichmentFile, ignored: boolean) {
		ignoreBusy = { ...ignoreBusy, [file.id]: true };
		try {
			if (ignored) {
				await ignoreEnrichmentFile(localStorage.token, file.id);
				toast.success(
					$i18n.t('"{name}" is now ignored and excluded from knowledge queries.', {
						name: file.title || file.filename || file.id
					})
				);
			} else {
				await unignoreEnrichmentFile(localStorage.token, file.id);
				toast.success(
					$i18n.t('"{name}" is restored and will be re-embedded.', {
						name: file.title || file.filename || file.id
					})
				);
			}
			await refreshIgnoredFiles();
		} catch (e) {
			toast.error(String(e));
		} finally {
			ignoreBusy = { ...ignoreBusy, [file.id]: false };
		}
	}

	async function loadKbFiles(knowledgeId: string) {
		if (!knowledgeId) {
			kbFiles = [];
			return;
		}
		kbFilesLoading = true;
		const myToken = ++kbFilesFetchToken;
		try {
			const list = await getKnowledgeFiles(localStorage.token, knowledgeId, '', 1, 200);
			if (myToken !== kbFilesFetchToken) return; // stale response
			kbFiles = Array.isArray(list) ? list : ((list as any)?.items ?? []);
		} catch (e) {
			console.error('loadKbFiles failed:', e);
			if (myToken === kbFilesFetchToken) kbFiles = [];
		} finally {
			if (myToken === kbFilesFetchToken) kbFilesLoading = false;
		}
	}

	$: if (scanKnowledgeId !== prevKb) {
		prevKb = scanKnowledgeId;
		selectedKbFileId = '';
		kbFileFilter = '';
		if (scanMode === 'file' && scanKnowledgeId) loadKbFiles(scanKnowledgeId);
	}
	$: if (scanMode === 'file' && scanKnowledgeId && kbFiles.length === 0 && !kbFilesLoading) {
		loadKbFiles(scanKnowledgeId);
	}
	// Reload the ignored-files list when KB changes or the "show ignored only" toggle flips.
	let _ignoredReloadKb = '';
	$: if (scanKnowledgeId) {
		const key = `${scanKnowledgeId}|${showIgnoredOnly ? 1 : 0}`;
		if (key !== _ignoredReloadKb) {
			_ignoredReloadKb = key;
			refreshIgnoredFiles();
		}
	} else if (_ignoredReloadKb !== '') {
		_ignoredReloadKb = '';
		enrichmentFiles = [];
	}

	onMount(async () => {
		const token = localStorage.token;
		try {
			const [kbResponse, mergedResponse, allResponse] = await Promise.all([
				getKnowledgeBases(token),
				getModels(token, null, true), // merged BASE models from all connections (owned_by set)
				getAllModels(token) // /models/all — workspace model records (My models)
			]);
			// /api/v1/knowledge/ returns a paginated envelope {items, total};
			// fall back to a bare array for forward-compat.
			knowledgeBases = Array.isArray(kbResponse) ? kbResponse : (kbResponse?.items ?? []);
			baseModels = mergedResponse ?? []; // getModels already unwraps .data
			workspaceModels = Array.isArray(allResponse) ? allResponse : (allResponse?.items ?? []);
		} catch (e) {
			console.error(e);
		}
		await refresh();
		if (latestScan?.status === 'running') startPolling();
		loaded = true;
	});

	onDestroy(stopPolling);
</script>

<AdminSettingSection title={$i18n.t('Run a Metadata Enrichment')}>
	<div
		class="flex items-start gap-2 p-3 mb-4 rounded-lg bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-900/50"
	>
		<span class="text-amber-500 mt-0.5">⚠</span>
		<div class="text-xs text-amber-700 dark:text-amber-300/80">
			<div class="font-medium mb-0.5">{$i18n.t('LLM cost warning')}</div>
			{$i18n.t(
				'Each file in scope sends one inference request to the selected model (a ~6,000-character excerpt per file). Large knowledge bases on metered models can cost significant tokens. Locally-hosted models avoid this. Parallelism is limited below.'
			)}
		</div>
	</div>

	<div class="space-y-3 flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		<div class="grid grid-cols-2 gap-3">
			<select
				class="text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5"
				bind:value={scanKnowledgeId}
				on:change={refresh}
			>
				<option value="">{$i18n.t('Select knowledge base…')}</option>
				{#each knowledgeBases as kb (kb.id)}
					<option value={kb.id}>{kb.name}</option>
				{/each}
			</select>
			<select
				class="text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5"
				bind:value={scanModelId}
			>
				<option value="">{$i18n.t('Select model…')}</option>
				{#if wrapperModels.length}
					<optgroup label={$i18n.t('My models')}>
						{#each wrapperModels as model (model.id)}
							<option value={model.id}>{model.name}</option>
						{/each}
					</optgroup>
				{/if}
				{#each baseGroups as [label, groupModels] (label)}
					<optgroup {label}>
						{#each groupModels as model (model.id)}
							<option value={model.id}>{model.name}</option>
						{/each}
					</optgroup>
				{/each}
			</select>
		</div>

		<select
			class="w-full text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5"
			bind:value={scanMode}
		>
			{#each Object.entries(modeLabels) as [mode, label]}
				<option value={mode}>{label}</option>
			{/each}
		</select>

		{#if scanMode === 'attributes'}
			<div class="flex flex-wrap gap-2">
				{#each ATTRIBUTE_OPTIONS as attr (attr.id)}
					<button
						type="button"
						class="text-xs px-3 py-1.5 rounded-full border transition {selectedAttributes.includes(
							attr.id
						)
							? 'bg-black dark:bg-white text-white dark:text-black border-transparent'
							: 'border-gray-200 dark:border-gray-850 hover:bg-gray-50 dark:hover:bg-gray-850'}"
						on:click={() => {
							selectedAttributes = selectedAttributes.includes(attr.id)
								? selectedAttributes.filter((a) => a !== attr.id)
								: [...selectedAttributes, attr.id];
						}}
					>
						{attr.label}
					</button>
				{/each}
			</div>
		{:else if scanMode === 'file'}
			<div class="relative">
				<input
					class="w-full text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5 pr-9"
					placeholder={selectedKbFileId ? selectedFileName : $i18n.t('Search files by name or ID…')}
					bind:value={kbFileFilter}
					on:focus={() => (fileDropdownOpen = true)}
				/>
				<button
					type="button"
					class="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
					on:click={() => (fileDropdownOpen = !fileDropdownOpen)}
					aria-label={$i18n.t('Browse files')}
				>
					{#if fileDropdownOpen}▲{:else}▼{/if}
				</button>
				{#if fileDropdownOpen}
					<div class="fixed inset-0 z-20" on:click={() => (fileDropdownOpen = false)}></div>
					<div
						class="absolute left-0 right-0 top-full mt-1 z-30 max-h-40 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-850 bg-white dark:bg-gray-950 shadow-lg divide-y divide-gray-100 dark:divide-gray-850"
					>
						{#if kbFilesLoading}
							<div class="px-3 py-2 text-xs text-gray-400">{$i18n.t('Loading files…')}</div>
						{:else if filteredKbFiles.length === 0}
							<div class="px-3 py-2 text-xs text-gray-400">
								{scanKnowledgeId
									? $i18n.t('No files match this filter in the selected knowledge base.')
									: $i18n.t('Select a knowledge base to load its files.')}
							</div>
						{:else}
							{#each filteredKbFiles as file (file.id)}
								<button
									type="button"
									class="w-full text-left px-3 py-2 text-xs bg-white dark:bg-gray-950 hover:bg-gray-50 dark:hover:bg-gray-850 flex items-center justify-between gap-2 {selectedKbFileId ===
									file.id
										? 'bg-blue-50 dark:bg-blue-950/30'
										: ''}"
									on:click={() => {
										selectedKbFileId = file.id;
										fileDropdownOpen = false;
									}}
								>
									<span class="truncate flex-1">
										<span class="font-medium">{file.filename || $i18n.t('(untitled)')}</span>
										<span class="ml-2 text-gray-400 font-mono">{file.id.slice(0, 8)}…</span>
									</span>
									{#if selectedKbFileId === file.id}
										<span class="text-blue-500">✓</span>
									{/if}
								</button>
							{/each}
						{/if}
					</div>
				{/if}
			</div>
			{#if selectedKbFileId}
				<div class="text-xs text-gray-400">
					{$i18n.t('Selected')}: <span class="font-medium">{selectedFileName}</span>
					<span class="ml-1 font-mono">({selectedKbFileId.slice(0, 8)}…)</span>
				</div>
			{/if}
		{/if}

		<!-- New scan behaviour flags (automatic mode). -->
		<div class="rounded-lg border border-gray-100 dark:border-gray-850 p-3 space-y-2">
			<div class="text-xs text-gray-500 dark:text-gray-400 font-medium mb-1">
				{$i18n.t('Options')}
			</div>
			<AdminSettingField
				label={$i18n.t('Improve file content')}
				description={$i18n.t(
					'Let the model rewrite the file body to fix formatting, OCR artefacts, or short answers. PII is still redacted.'
				)}
			>
				<Switch bind:state={improveContent} />
			</AdminSettingField>
			<AdminSettingField
				label={$i18n.t('Improve titles')}
				description={$i18n.t(
					'Update the embedded title (meta.title) so queries match more precisely.'
				)}
			>
				<Switch bind:state={updateTitles} />
			</AdminSettingField>
			<AdminSettingField
				label={$i18n.t('Ignore low-value files')}
				description={$i18n.t(
					'Files the model marks as empty, link-only, or otherwise not worth indexing are flagged ignored automatically.'
				)}
			>
				<Switch bind:state={ignoreLowValue} />
			</AdminSettingField>
			<AdminSettingField
				label={$i18n.t('Re-enrich files already processed')}
				description={$i18n.t(
					'By default files enriched in a recent scan are skipped. Turn this on to force re-processing.'
				)}
			>
				<Switch bind:state={forceReenrich} />
			</AdminSettingField>
			<AdminSettingField
				label={$i18n.t('Include ignored files')}
				description={$i18n.t(
					'Re-run enrichment on files currently flagged ignored. Off by default; ignored files are excluded.'
				)}
			>
				<Switch bind:state={includeIgnored} />
			</AdminSettingField>
		</div>

		<AdminSettingField
			label={$i18n.t('Max parallel inference requests')}
			description={$i18n.t(
				'Keep at 1 for a single locally-hosted model. The server enforces its own ceiling (knowledge.metadata.max_parallel, default 1) regardless of this value.'
			)}
		>
			<input
				type="number"
				min="1"
				max="8"
				class="w-20 text-sm text-center rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2"
				bind:value={maxParallel}
			/>
		</AdminSettingField>

		<AdminSettingField
			label={$i18n.t('I understand the potential LLM cost')}
			description={$i18n.t('Required before a scan can start.')}
		>
			<Switch bind:state={acknowledgedCost} />
		</AdminSettingField>

		<div class="flex flex-wrap gap-2 items-center">
			<button
				type="button"
				class="px-3.5 py-1.5 text-sm font-medium bg-black dark:bg-white text-white dark:text-black rounded-lg disabled:opacity-50"
				disabled={busy}
				on:click={runScan}
			>
				{$i18n.t('Enrich')}
			</button>
			<button
				type="button"
				class="px-3.5 py-1.5 text-sm font-medium rounded-lg border border-gray-200 dark:border-gray-850 hover:bg-gray-50 dark:hover:bg-gray-850 disabled:opacity-50"
				disabled={busy}
				on:click={runTest}
			>
				{$i18n.t('Test run (1 file)')}
			</button>
		</div>
		<p class="text-xs text-gray-400 mt-2">
			{$i18n.t(
				'Enrichment runs automatically: changes are applied directly without a review step, changed files are re-embedded (re-indexed), and the model may also improve file content. Ignored files are excluded from knowledge queries.'
			)}
		</p>

		{#if latestScan}
			<div class="mt-4 p-3 rounded-xl border border-gray-100 dark:border-gray-850 text-xs">
				<div class="flex items-center justify-between mb-2">
					<span class="font-medium">
						{latestScan.test_run ? 'Test run (1 file) · ' : ''}{modeLabels[latestScan.mode] ||
							latestScan.mode}
						{#if latestScan.status === 'running'}
							<span class="text-blue-500">· {$i18n.t('running')}</span>
						{:else if latestScan.status === 'completed'}
							<span class="text-green-500">· {$i18n.t('completed')}</span>
						{:else}
							<span class="text-red-500">· {latestScan.status}</span>
						{/if}
					</span>
					<span class="text-gray-400">
						{latestScan.processed_files}/{latestScan.total_files}
						· {latestScan.proposals_created}
						{$i18n.t('changed')}
						· {latestScan.redactions}
						{$i18n.t('redacted')}
						· {latestScan.reembedded}
						{$i18n.t('re-embedded')}
					</span>
				</div>
				<!-- New automatic-mode breakdown row. -->
				<div class="flex flex-wrap gap-x-3 gap-y-0.5 text-gray-400 mb-2">
					<span>
						<span class="text-gray-700 dark:text-gray-200 font-medium"
							>{latestScan.content_updated ?? 0}</span
						>
						{$i18n.t('content updated')}
					</span>
					<span>
						<span class="text-gray-700 dark:text-gray-200 font-medium"
							>{latestScan.titles_updated ?? 0}</span
						>
						{$i18n.t('titles updated')}
					</span>
					<span>
						<span class="text-gray-700 dark:text-gray-200 font-medium"
							>{latestScan.ignored ?? 0}</span
						>
						{$i18n.t('ignored')}
					</span>
				</div>
				{#if latestScan.errors?.length}
					<div class="mt-2 text-red-500 dark:text-red-400 space-y-0.5">
						{#each latestScan.errors.slice(0, 5) as err}
							<div class="truncate" title={err}>{err}</div>
						{/each}
						{#if latestScan.errors.length > 5}
							<div class="text-gray-400">+{latestScan.errors.length - 5} more (see server log)</div>
						{/if}
					</div>
				{/if}
				<div class="h-1.5 rounded-full bg-gray-100 dark:bg-gray-850 overflow-hidden">
					<div
						class="h-full bg-black dark:bg-white transition-all"
						style="width: {latestScan.total_files
							? Math.round((latestScan.processed_files / latestScan.total_files) * 100)
							: 0}%"
					/>
				</div>

				{#if latestScan.status === 'running'}
					<div class="mt-2 text-right">
						<button
							type="button"
							class="text-xs px-2.5 py-1 rounded-lg border border-gray-200 dark:border-gray-850 hover:bg-gray-50 dark:hover:bg-gray-850"
							on:click={cancelScan}
						>
							{$i18n.t('Cancel scan')}
						</button>
					</div>
				{/if}
			</div>
		{/if}
	</div>
	{#if latestScan?.test_preview}
		<div
			class="mt-3 p-3 rounded-xl border border-blue-200 dark:border-blue-900/50 bg-blue-50/50 dark:bg-blue-950/20 text-xs space-y-2"
		>
			<div class="font-medium">
				{$i18n.t('Test run preview')} · {latestScan.test_preview.filename}
			</div>
			<div class="grid gap-1">
				{#each Object.entries(latestScan.test_preview.proposed ?? {}) as [k, v] (k)}
					{#if v}
						<div><span class="text-gray-400">{k}:</span> {v}</div>
					{/if}
				{/each}
			</div>
			<div class="pt-1 border-t border-blue-200 dark:border-blue-900/50">
				<div class="text-gray-400 mb-0.5">{$i18n.t('Embedding header - before')}:</div>
				<div class="font-mono text-[10px] break-all">{latestScan.test_preview.header_before}</div>
				<div class="text-gray-400 mt-1.5 mb-0.5">{$i18n.t('Embedding header - after')}:</div>
				<div class="font-mono text-[10px] break-all">{latestScan.test_preview.header_after}</div>
			</div>
			<!-- Content before/after, rendered defensively (keys may be absent). -->
			{#if latestScan.test_preview.content_before != null || latestScan.test_preview.content_after != null}
				<div class="pt-1 border-t border-blue-200 dark:border-blue-900/50">
					<div class="text-gray-400 mb-0.5">{$i18n.t('Content - before')}:</div>
					<div
						class="font-mono text-[10px] whitespace-pre-wrap break-words max-h-40 overflow-y-auto"
					>
						{latestScan.test_preview.content_before ?? ''}
					</div>
					<div class="text-gray-400 mt-1.5 mb-0.5">{$i18n.t('Content - after')}:</div>
					<div
						class="font-mono text-[10px] whitespace-pre-wrap break-words max-h-40 overflow-y-auto"
					>
						{latestScan.test_preview.content_after ?? ''}
					</div>
				</div>
			{/if}
		</div>
	{/if}
</AdminSettingSection>

<AdminSettingSection title={$i18n.t('Ignored files')}>
	<p class="text-xs text-gray-500 dark:text-gray-400">
		{$i18n.t(
			'Ignored files stay in the knowledge base but are excluded from knowledge queries. Use this for empty documents, link-only pages, or other content that is not worth indexing. You can restore a file at any time.'
		)}
	</p>

	<div class="flex items-center gap-3 flex-wrap mt-1">
		<label
			class="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400 cursor-pointer"
		>
			<input type="checkbox" bind:checked={showIgnoredOnly} class="w-3.5 h-3.5 accent-blue-500" />
			{$i18n.t('Show ignored files only')}
		</label>
		<button
			type="button"
			class="text-xs px-2.5 py-1 rounded-lg border border-gray-200 dark:border-gray-850 hover:bg-gray-50 dark:hover:bg-gray-850 disabled:opacity-50"
			disabled={enrichmentFilesLoading || !scanKnowledgeId}
			on:click={refreshIgnoredFiles}
		>
			{$i18n.t('Refresh')}
		</button>
	</div>

	{#if !scanKnowledgeId}
		<div class="text-xs text-gray-400">
			{$i18n.t('Select a knowledge base above to see its files.')}
		</div>
	{:else if enrichmentFilesLoading}
		<div class="text-xs text-gray-400">{$i18n.t('Loading files…')}</div>
	{:else if enrichmentFiles.length === 0}
		<div class="text-xs text-gray-400">
			{showIgnoredOnly
				? $i18n.t('No ignored files.')
				: $i18n.t('No files in this knowledge base yet.')}
		</div>
	{:else}
		<div class="rounded-lg border border-gray-100 dark:border-gray-850 overflow-hidden">
			<div
				class="grid grid-cols-[1fr_auto_auto_auto] gap-2 px-3 py-1.5 text-[0.6875rem] uppercase tracking-wide text-gray-400 bg-gray-50 dark:bg-gray-900"
			>
				<span>{$i18n.t('File')}</span>
				<span>{$i18n.t('Size')}</span>
				<span>{$i18n.t('Reason')}</span>
				<span>{$i18n.t('Action')}</span>
			</div>
			<div class="divide-y divide-gray-100 dark:divide-gray-850">
				{#each enrichmentFiles as file (file.id)}
					<div class="grid grid-cols-[1fr_auto_auto_auto] gap-2 px-3 py-1.5 text-xs items-center">
						<span class="min-w-0 truncate" title={file.title || file.filename || file.id}>
							<span class="font-medium">
								{file.title || file.filename || $i18n.t('(untitled)')}
							</span>
							{#if file.title && file.filename}
								<span class="ml-1 text-gray-400 truncate">{file.filename}</span>
							{/if}
						</span>
						<span class="text-gray-400 tabular-nums">
							{file.size != null ? formatFileSize(file.size) : '—'}
						</span>
						<span class="text-gray-400">
							{file.ignored
								? IGNORED_REASON_LABELS[file.ignored_reason ?? ''] || (file.ignored_reason ?? '—')
								: '—'}
						</span>
						<span>
							{#if file.ignored}
								<button
									type="button"
									class="text-xs px-2.5 py-1 rounded-lg border border-gray-200 dark:border-gray-850 hover:bg-gray-50 dark:hover:bg-gray-850 disabled:opacity-50"
									disabled={ignoreBusy[file.id]}
									on:click={() => setFileIgnored(file, false)}
								>
									{$i18n.t('Unignore')}
								</button>
							{:else}
								<button
									type="button"
									class="text-xs px-2.5 py-1 rounded-lg border border-gray-200 dark:border-gray-850 hover:bg-gray-50 dark:hover:bg-gray-850 disabled:opacity-50"
									disabled={ignoreBusy[file.id]}
									on:click={() => setFileIgnored(file, true)}
								>
									{$i18n.t('Ignore')}
								</button>
							{/if}
						</span>
					</div>
				{/each}
			</div>
		</div>
	{/if}
</AdminSettingSection>
