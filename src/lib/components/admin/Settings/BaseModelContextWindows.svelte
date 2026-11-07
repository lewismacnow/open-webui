<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import {
		getBaseModelContextWindows,
		setBaseModelContextWindows,
		type BaseModelContextWindowsConfig
	} from '$lib/apis/configs';
	import { getBaseModels } from '$lib/apis/models';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	// The maximum context of every BASE model. A wrapper model inherits its
	// base model's window and may only lower it, and never exceed the
	// smallest window in the failover chain that could serve it.
	let config: BaseModelContextWindowsConfig = { default: 256000, windows: {} };
	let baseModels: any[] = [];
	let loaded = false;
	let saving = false;
	let dirty = false;

	// Per-model edit values, keyed by model id. An empty value means "use
	// the default".
	let edits: Record<string, number | ''> = {};

	const formatTokens = (value: number) => {
		if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2).replace(/\.?0+$/, '')}M`;
		if (value >= 1_000) return `${(value / 1_000).toFixed(1).replace(/\.0$/, '')}K`;
		return String(value);
	};

	const windowFor = (modelId: string): number =>
		edits[modelId] === '' || edits[modelId] === undefined ? config.default : Number(edits[modelId]);

	const load = async () => {
		try {
			const [loadedConfig, models] = await Promise.all([
				getBaseModelContextWindows(localStorage.token),
				getBaseModels(localStorage.token).catch(() => [])
			]);
			config = loadedConfig;
			baseModels = (models ?? []).filter((m: any) => m?.id);
			edits = Object.fromEntries(baseModels.map((m: any) => [m.id, config.windows[m.id] ?? '']));
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loaded = true;
		}
	};

	onMount(load);

	const save = async () => {
		saving = true;
		try {
			const windows: Record<string, number> = {};
			for (const [modelId, value] of Object.entries(edits)) {
				if (value === '' || value === null || value === undefined) continue;
				const tokens = Number(value);
				if (!Number.isFinite(tokens) || tokens <= 0) continue;
				windows[modelId] = Math.floor(tokens);
			}
			config = await setBaseModelContextWindows(localStorage.token, {
				default: config.default,
				windows
			});
			dirty = false;
			toast.success($i18n.t('Base model context windows saved'));
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			saving = false;
		}
	};
</script>

<AdminSettingSection title={$i18n.t('Base Model Context Window')}>
	{#if !loaded}
		<div class="flex justify-center py-4">
			<Spinner />
		</div>
	{:else}
		<div class="space-y-3">
			<AdminSettingField
				label={$i18n.t('Default maximum context')}
				description={$i18n.t('Default maximum context window')}
			>
				<div class="flex items-center gap-2">
					<input
						type="number"
						min="1000"
						step="1000"
						class="w-40 text-sm bg-transparent outline-none border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
						bind:value={config.default}
						on:change={() => (dirty = true)}
						aria-label={$i18n.t('Default maximum context')}
					/>
					<span class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('tokens')} ({formatTokens(Number(config.default) || 0)})
					</span>
				</div>
			</AdminSettingField>

			<div
				class="rounded-lg border border-gray-200 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-850/50 px-3 py-2"
			>
				<div class="text-xs text-gray-600 dark:text-gray-400 mb-2">
					{$i18n.t('Base model context windows')}
				</div>

				{#if baseModels.length === 0}
					<div class="text-xs text-gray-500 dark:text-gray-400 py-1">
						{$i18n.t('No base models found. Add a connection first.')}
					</div>
				{:else}
					<div class="max-h-80 overflow-y-auto divide-y divide-gray-200 dark:divide-gray-800">
						{#each baseModels as m (m.id)}
							<div class="flex items-center gap-3 py-1.5">
								<div class="min-w-0 flex-1">
									<div class="truncate text-xs text-gray-800 dark:text-gray-100">
										{m.name || m.id}
									</div>
									<div class="truncate text-[0.6875rem] font-mono text-gray-400 dark:text-gray-600">
										{m.id}
									</div>
								</div>
								<div class="flex items-center gap-2 shrink-0">
									<input
										type="number"
										min="1000"
										step="1000"
										class="w-32 text-xs text-right bg-transparent outline-none border border-gray-200 dark:border-gray-700 rounded px-2 py-1"
										placeholder={formatTokens(config.default)}
										value={edits[m.id] ?? ''}
										on:change={(e) => {
											edits[m.id] = (e.currentTarget as HTMLInputElement).value as any;
											dirty = true;
										}}
										aria-label={$i18n.t('Maximum context')}
									/>
									<span class="w-12 text-xs text-gray-500 dark:text-gray-400">
										{formatTokens(windowFor(m.id))}
									</span>
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</div>

			<div class="flex justify-end">
				<button
					class="px-3 py-1.5 text-xs font-medium rounded-lg transition bg-gray-900 text-white dark:bg-white dark:text-gray-900 disabled:opacity-40"
					type="button"
					on:click={save}
					disabled={saving || !dirty}
				>
					{saving ? $i18n.t('Saving…') : $i18n.t('Save')}
				</button>
			</div>
		</div>
	{/if}
</AdminSettingSection>
