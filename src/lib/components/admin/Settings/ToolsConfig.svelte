<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getToolsConfig, setToolsConfig, type ToolsConfig } from '$lib/apis/configs';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	// Knobs for the built-in knowledge tools (grep / view / exec). All eight
	// are persisted via POST /api/v1/configs/tools; only the three regex
	// knobs are picked up live by the running process — the rest are read
	// at backend start until env.py reads them from the same cache.
	let toolsConfig: ToolsConfig = {
		match_budget_seconds: 5,
		max_regex_quantifier_count: 2000,
		max_regex_quantifier_expansion: 100000,
		kb_exec_max_output_chars: 30000,
		kb_exec_max_grep_files: 200,
		knowledge_grep_max_matches: 50,
		view_file_max_chars: 100000,
		view_file_default_max_chars: 10000
	};
	let loaded = false;
	let saving = false;

	// Debounced autosave (ApiTools-style change-driven save, with a short
	// debounce because number inputs fire many change events).
	let saveTimer: ReturnType<typeof setTimeout> | null = null;

	const inputClass =
		'w-28 h-7 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';

	function scheduleSave() {
		if (!loaded) return;
		if (saveTimer) clearTimeout(saveTimer);
		saveTimer = setTimeout(() => {
			saveTimer = null;
			save();
		}, 800);
	}

	async function save() {
		if (saveTimer) {
			clearTimeout(saveTimer);
			saveTimer = null;
		}
		saving = true;
		try {
			// Coerce cleared inputs (null) to 0; the backend clamps each
			// field to its floor and returns the cleaned form.
			const cleaned = {
				match_budget_seconds: Number(toolsConfig.match_budget_seconds) || 0,
				max_regex_quantifier_count: Number(toolsConfig.max_regex_quantifier_count) || 0,
				max_regex_quantifier_expansion: Number(toolsConfig.max_regex_quantifier_expansion) || 0,
				kb_exec_max_output_chars: Number(toolsConfig.kb_exec_max_output_chars) || 0,
				kb_exec_max_grep_files: Number(toolsConfig.kb_exec_max_grep_files) || 0,
				knowledge_grep_max_matches: Number(toolsConfig.knowledge_grep_max_matches) || 0,
				view_file_max_chars: Number(toolsConfig.view_file_max_chars) || 0,
				view_file_default_max_chars: Number(toolsConfig.view_file_default_max_chars) || 0
			};
			toolsConfig = await setToolsConfig(localStorage.token, cleaned);
			toast.success($i18n.t('Tools settings saved'));
		} catch (e) {
			toast.error($i18n.t('Failed to save Tools settings'));
		} finally {
			saving = false;
		}
	}

	onMount(async () => {
		try {
			toolsConfig = await getToolsConfig(localStorage.token);
		} catch (e) {
			console.error('Failed to load tools config:', e);
			toast.error($i18n.t('Failed to load Tools settings'));
		}
		loaded = true;
	});
</script>

<form
	class="flex h-full flex-col justify-between text-sm"
	on:submit|preventDefault={() => {
		save();
	}}
>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('Tools Config')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		{#if !loaded}
			<div class="flex justify-center py-6">
				<Spinner />
			</div>
		{:else}
			<AdminSettingSection title={$i18n.t('Regex Matching')} first>
				<p class="text-sm text-gray-500 dark:text-gray-400 mb-2">
					{$i18n.t(
						'Knobs for the built-in knowledge tools (grep / view / exec). Live knobs take effect on the next tool call.'
					)}
				</p>

				<AdminSettingField
					label={$i18n.t('Match budget (seconds)')}
					description={$i18n.t(
						'Soft limit on per-call regex matching time. Default 5. Higher = wider regex tolerance, lower = faster fail. Live — takes effect on next tool call.'
					)}
				>
					<input
						class={inputClass}
						type="number"
						step="0.5"
						min="0.1"
						bind:value={toolsConfig.match_budget_seconds}
						on:change={scheduleSave}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('Max regex quantifier count')}
					description={$i18n.t(
						'Safety net for catastrophic backtracking. Default 2,000. Live — takes effect on next tool call.'
					)}
				>
					<input
						class={inputClass}
						type="number"
						step="1"
						min="100"
						bind:value={toolsConfig.max_regex_quantifier_count}
						on:change={scheduleSave}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('Max regex quantifier expansion')}
					description={$i18n.t(
						'Safety net for catastrophic backtracking. Default 100,000. Live — takes effect on next tool call.'
					)}
				>
					<input
						class={inputClass}
						type="number"
						step="1"
						min="100"
						bind:value={toolsConfig.max_regex_quantifier_expansion}
						on:change={scheduleSave}
					/>
				</AdminSettingField>
			</AdminSettingSection>

			<AdminSettingSection title={$i18n.t('Output Caps')}>
				<p class="text-xs text-amber-600 dark:text-amber-400">
					{$i18n.t(
						'These caps are read at backend startup — changing them requires a backend restart to take effect.'
					)}
				</p>

				<AdminSettingField
					label={$i18n.t('KB exec max output chars')}
					description={$i18n.t(
						'Cap on total output characters returned by grep/view. Default 30,000. Requires backend restart.'
					)}
				>
					<input
						class={inputClass}
						type="number"
						step="1"
						min="1000"
						bind:value={toolsConfig.kb_exec_max_output_chars}
						on:change={scheduleSave}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('KB exec max grep files')}
					description={$i18n.t(
						'Cap on number of files scanned per grep call. Default 200. Requires backend restart.'
					)}
				>
					<input
						class={inputClass}
						type="number"
						step="1"
						min="1"
						bind:value={toolsConfig.kb_exec_max_grep_files}
						on:change={scheduleSave}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('Knowledge grep max matches')}
					description={$i18n.t('Cap matches per file. Default 50. Requires backend restart.')}
				>
					<input
						class={inputClass}
						type="number"
						step="1"
						min="1"
						bind:value={toolsConfig.knowledge_grep_max_matches}
						on:change={scheduleSave}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('View file max chars')}
					description={$i18n.t(
						'Absolute cap per file (across pagination). Default 100,000. Requires backend restart.'
					)}
				>
					<input
						class={inputClass}
						type="number"
						step="1"
						min="1000"
						bind:value={toolsConfig.view_file_max_chars}
						on:change={scheduleSave}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('View file default max chars')}
					description={$i18n.t(
						'Default chars returned when offset=0. Default 10,000. Requires backend restart.'
					)}
				>
					<input
						class={inputClass}
						type="number"
						step="1"
						min="100"
						bind:value={toolsConfig.view_file_default_max_chars}
						on:change={scheduleSave}
					/>
				</AdminSettingField>
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
