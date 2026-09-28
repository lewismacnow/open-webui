<script lang="ts">
	import { onMount, onDestroy, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
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
	import { getBaseModels } from '$lib/apis/models';

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
	let models: any[] = [];
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
				status: 'pending'
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

	function attrOf(p: MetadataProposal): string {
		const parts: string[] = [];
		if (p.proposed_title) parts.push('title');
		if (p.proposed_description) parts.push('description');
		if (p.proposed_summary) parts.push('summary');
		if (p.proposed_tags) parts.push('tags');
		return parts.join(', ');
	}

	onMount(async () => {
		const token = localStorage.token;
		try {
			[knowledgeBases, models] = await Promise.all([
				getKnowledgeBases(token),
				getBaseModels(token)
			]);
		} catch (e) {
			console.error(e);
		}
		await refresh();
		if (latestScan?.status === 'running') startPolling();
		loaded = true;
	});

	onDestroy(stopPolling);
</script>

<AdminSettingSection title={$i18n.t('Run a Metadata Scan')}>
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
				{#each models as model (model.id)}
					<option value={model.id}>{model.name}</option>
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
			<input
				class="w-full text-sm rounded-lg bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-850 p-2.5 font-mono"
				placeholder={$i18n.t('File ID (from the knowledge file list)')}
				bind:value={scanFileId}
			/>
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
			{$i18n.t('Start scan')}
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
		</div>
	{/if}
</AdminSettingSection>

<AdminSettingSection title={$i18n.t('Pending Proposals')}>
	<p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
		{$i18n.t(
			'Suggestions are inert until applied. PII and secrets found in suggestions are redacted to [pii-redacted] automatically — never rejected.'
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
					<div class="text-xs space-y-1">
						{#if proposal.proposed_title}
							<div>
								<span class="text-gray-400">{$i18n.t('Title')}:</span>
								<s class="text-gray-400">{proposal.previous_title || '—'}</s>
								→ <span class="font-medium">{proposal.proposed_title}</span>
							</div>
						{/if}
						{#if proposal.proposed_description}
							<div>
								<span class="text-gray-400">{$i18n.t('Description')}:</span>
								{proposal.proposed_description}
							</div>
						{/if}
						{#if proposal.proposed_summary}
							<div class="text-gray-500 dark:text-gray-400 line-clamp-2">
								<span class="text-gray-400">{$i18n.t('Summary')}:</span>
								{proposal.proposed_summary}
							</div>
						{/if}
						{#if proposal.proposed_tags}
							<div class="flex flex-wrap gap-1 pt-1">
								{#each proposal.proposed_tags as tag (tag)}
									<span class="px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-850 text-[10px]"
										>{tag}</span
									>
								{/each}
							</div>
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{:else}
		<div class="text-xs text-gray-400">{$i18n.t('No pending proposals.')}</div>
	{/if}
</AdminSettingSection>
