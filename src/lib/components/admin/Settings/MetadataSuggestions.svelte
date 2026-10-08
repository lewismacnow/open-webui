<script lang="ts">
	import { onMount, onDestroy, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		cancelMetadataScan,
		startMetadataScan,
		getMetadataScans,
		getMetadataProposals,
		applyMetadataProposal,
		dismissMetadataProposal,
		applyAllMetadataProposals,
		type MetadataScan,
		type MetadataProposal
	} from '$lib/apis/metadataSuggestions';
	import { getKnowledgeBases } from '$lib/apis/knowledge';
	import { getAllModels } from '$lib/apis/models';
	import { getModels } from '$lib/apis';
	import { getKnowledgeFiles } from '$lib/apis/knowledge';

	import Switch from '$lib/components/common/Switch.svelte';
	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	const ATTRIBUTE_OPTIONS = [
		{ id: 'title', label: 'Title' },
		{ id: 'description', label: 'Description' },
		{ id: 'summary', label: 'Summary' },
		{ id: 'tags', label: 'Tags' }
	];

	let knowledgeBases: any[] = [];
	let baseModels: any[] = [];
	let workspaceModels: any[] = [];
	// Proposal ids whose long-field (description/summary) diff is expanded.
	let expandedProposals = new Set<string>();
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

	// Progress + results
	let latestScan: MetadataScan | null = null;
	let proposals: MetadataProposal[] = [];
	let pollTimer: any = null;

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
		'openrouter': 'OpenRouter',
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
	$: filteredKbFiles = (() => {
		const q = kbFileFilter.trim().toLowerCase();
		if (!q) return kbFiles;
		return kbFiles.filter(
			(f) => (f?.filename ?? '').toLowerCase().includes(q) || (f?.id ?? '').toLowerCase().includes(q)
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
			proposals = await getMetadataProposals(token, {
				knowledgeId: scanKnowledgeId || null,
				status: showNoChange ? null : 'pending'
			});
		} catch (e) {
			console.error(e);
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
						$i18n.t('Scan complete: {p} proposals from {n} files', {
							p: latestScan.proposals_created,
							n: latestScan.processed_files
						})
					);
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
				max_parallel: maxParallel
			});
			toast.info($i18n.t('Scan started'));
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

	async function applyOne(proposal: MetadataProposal) {
		try {
			await applyMetadataProposal(localStorage.token, proposal.id);
			proposals = proposals.filter((p) => p.id !== proposal.id);
		} catch (e) {
			toast.error(String(e));
		}
	}

	async function dismissOne(proposal: MetadataProposal) {
		try {
			await dismissMetadataProposal(localStorage.token, proposal.id);
			proposals = proposals.filter((p) => p.id !== proposal.id);
		} catch (e) {
			toast.error(String(e));
		}
	}

	async function applyAll() {
		if (!confirm($i18n.t('Apply all pending proposals? Existing metadata will be overwritten.')))
			return;
		try {
			const res = await applyAllMetadataProposals(localStorage.token, scanKnowledgeId);
			toast.success($i18n.t('{n} proposals applied', { n: res.applied }));
			await refresh();
		} catch (e) {
			toast.error(String(e));
		}
	}

	function tagsEqual(a: string[] | null, b: string[] | null): boolean {
		const sa = [...(a ?? [])].sort();
		const sb = [...(b ?? [])].sort();
		return sa.length === sb.length && sa.every((v, i) => v === sb[i]);
	}

	function toggleExpanded(id: string) {
		if (expandedProposals.has(id)) {
			expandedProposals.delete(id);
		} else {
			expandedProposals.add(id);
		}
		expandedProposals = expandedProposals; // trigger reactivity
	}

	function attrOf(p: MetadataProposal): string {
		const parts: string[] = [];
		if (p.proposed_title) parts.push('title');
		if (p.proposed_description) parts.push('description');
		if (p.proposed_summary) parts.push('summary');
		if (p.proposed_tags) parts.push('tags');
		return parts.join(', ');
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
			kbFiles = Array.isArray(list) ? list : (list?.items ?? []);
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
	<div class="max-h-[calc(100vh-12rem)] overflow-y-auto pr-1">
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

	<div class="space-y-3">
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
					<optgroup label={label}>
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
			<div class="space-y-2">
				<input
					class="w-full text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5"
					placeholder={$i18n.t('Search files by name or ID…')}
					bind:value={kbFileFilter}
				/>
				{#if kbFilesLoading}
					<div class="text-xs text-gray-400 py-1">{$i18n.t('Loading files…')}</div>
				{:else if filteredKbFiles.length === 0}
					<div class="text-xs text-gray-400 py-1">
						{scanKnowledgeId
							? $i18n.t('No files match this filter in the selected knowledge base.')
							: $i18n.t('Select a knowledge base to load its files.')}
					</div>
				{:else}
					<div class="max-h-64 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-850 divide-y divide-gray-100 dark:divide-gray-850">
						{#each filteredKbFiles as file (file.id)}
							<button
								type="button"
								class="w-full text-left px-3 py-2 text-xs hover:bg-gray-50 dark:hover:bg-gray-850 flex items-center justify-between gap-2 {selectedKbFileId === file.id ? 'bg-blue-50 dark:bg-blue-950/30' : ''}"
								on:click={() => (selectedKbFileId = file.id)}
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
					</div>
				{/if}
				{#if selectedKbFileId}
					<div class="text-xs text-gray-400">
						{$i18n.t('Selected')}: <span class="font-mono">{selectedKbFileId}</span>
					</div>
				{/if}
			</div>
		{/if}

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

		<button
			type="button"
			class="px-3.5 py-1.5 text-sm font-medium bg-black dark:bg-white text-white dark:text-black rounded-lg disabled:opacity-50"
			disabled={busy}
			on:click={runScan}
		>
			{$i18n.t('Enrich selected files')}
		</button>
	</div>

	{#if latestScan}
		<div class="mt-4 p-3 rounded-xl border border-gray-100 dark:border-gray-850 text-xs">
			<div class="flex items-center justify-between mb-2">
				<span class="font-medium">
					{modeLabels[latestScan.mode] || latestScan.mode}
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
					{$i18n.t('proposals')}
					· {latestScan.redactions}
					{$i18n.t('redacted')}
				</span>
			</div>
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

	</div></AdminSettingSection>

<AdminSettingSection title={$i18n.t('Pending changes')}>
	<p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
		{$i18n.t(
			'When the model finds a missing field, the value is applied directly. Existing fields are queued for review. PII/secrets in proposals are redacted to [pii-redacted] automatically.'
		)}
	</p>

	{#if proposals.length}
		<div class="flex justify-end mb-2">
			<button
				type="button"
				class="text-xs px-3 py-1.5 rounded-lg bg-black dark:bg-white text-white dark:text-black"
				on:click={applyAll}
			>
				{$i18n.t('Apply all')}
			</button>
		</div>
		<div class="space-y-2">
			{#each proposals as proposal (proposal.id)}
				<div class="p-3 rounded-xl border border-gray-100 dark:border-gray-850">
					<div class="flex items-center justify-between gap-3 mb-2">
						<span class="text-xs font-mono text-gray-400 truncate">
							{proposal.file_id.slice(0, 8)}…
							<span class="ml-2 not-italic text-blue-500">{attrOf(proposal)}</span>
							{#if proposal.redaction_count > 0}
								<span class="ml-2 text-amber-500"
									>· {proposal.redaction_count} {$i18n.t('redactions')}</span
								>
							{/if}
						</span>
						<div class="flex gap-1.5 shrink-0">
							<button
								type="button"
								class="text-xs px-2.5 py-1 rounded-lg bg-black dark:bg-white text-white dark:text-black"
								on:click={() => applyOne(proposal)}
							>
								{$i18n.t('Apply')}
							</button>
							<button
								type="button"
								class="text-xs px-2.5 py-1 rounded-lg border border-gray-200 dark:border-gray-850 hover:bg-gray-50 dark:hover:bg-gray-850"
								on:click={() => dismissOne(proposal)}
							>
								{$i18n.t('Dismiss')}
							</button>
						</div>
					</div>
					<div class="text-xs space-y-1.5">
						{#if proposal.proposed_title}
							<div>
								<span class="text-gray-400">{$i18n.t('Title')}:</span>
								{#if proposal.previous_title && proposal.previous_title !== proposal.proposed_title}
									<s class="text-gray-400 mr-1">{proposal.previous_title}</s>
									<span class="text-gray-400 mr-1">→</span>
								{/if}
								<span class="font-medium">{proposal.proposed_title}</span>
							</div>
						{/if}

						{#if proposal.proposed_description}
							<div>
								<span class="text-gray-400">{$i18n.t('Description')}:</span>
								{#if proposal.previous_description && proposal.previous_description !== proposal.proposed_description}
									<button
										type="button"
										class="ml-1 underline text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
										on:click={() => toggleExpanded(proposal.id)}
									>
										{expandedProposals.has(proposal.id)
											? $i18n.t('hide before')
											: $i18n.t('show before')}
									</button>
								{/if}
								{#if proposal.previous_description && proposal.previous_description !== proposal.proposed_description && expandedProposals.has(proposal.id)}
									<s class="block text-gray-400 line-clamp-3 mt-0.5">{proposal
										.previous_description}</s>
								{/if}
								<span class="block mt-0.5">{proposal.proposed_description}</span>
							</div>
						{/if}

						{#if proposal.proposed_summary}
							<div>
								<span class="text-gray-400">{$i18n.t('Summary')}:</span>
								{#if proposal.previous_summary && proposal.previous_summary !== proposal.proposed_summary}
									<button
										type="button"
										class="ml-1 underline text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
										on:click={() => toggleExpanded(proposal.id)}
									>
										{expandedProposals.has(proposal.id)
											? $i18n.t('hide before')
											: $i18n.t('show before')}
									</button>
								{/if}
								{#if proposal.previous_summary && proposal.previous_summary !== proposal.proposed_summary && expandedProposals.has(proposal.id)}
									<s class="block text-gray-400 line-clamp-3 mt-0.5">{proposal
										.previous_summary}</s>
								{/if}
								<span class="block mt-0.5 line-clamp-3">{proposal.proposed_summary}</span>
							</div>
						{/if}

						{#if proposal.proposed_tags && !tagsEqual(proposal.proposed_tags, proposal.previous_tags)}
							<div class="flex flex-wrap gap-1 pt-0.5">
								{#each proposal.previous_tags ?? [] as tag (tag)}
									{#if !proposal.proposed_tags.includes(tag)}
										<span class="px-2 py-0.5 rounded-full bg-red-100 dark:bg-red-950/40 text-red-600 dark:text-red-300 text-[10px] line-through">{tag}</span>
										{/if}
								{/each}
								{#each proposal.proposed_tags as tag (tag)}
									{#if (proposal.previous_tags ?? []).includes(tag)}
										<span class="px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-850 text-gray-500 dark:text-gray-400 text-[10px]">{tag}</span>
									{:else}
										<span class="px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-950/40 text-green-700 dark:text-green-300 text-[10px]">+{tag}</span>
									{/if}
								{/each}
							</div>
						{:else if proposal.proposed_tags}
							<div class="flex flex-wrap gap-1 pt-0.5">
								{#each proposal.proposed_tags as tag (tag)}
									<span class="px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-850 text-[10px]">{tag}</span>
								{/each}
							</div>
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{:else}
		<div class="text-xs text-gray-400">{$i18n.t('No pending changes. Run a scan to suggest enrichments.')}</div>
	{/if}
</AdminSettingSection>
