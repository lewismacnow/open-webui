<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		getFailoverQueueConfig,
		setFailoverQueueConfig,
		type FailoverQueueConfig
	} from '$lib/apis/configs';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	// Defaults mirror the backend fallbacks (see getFailoverQueueConfig).
	// Used as the initial form state and as the placeholder text in the
	// textarea so users can see what a fresh install looks like.
	const DEFAULT_FULL_MESSAGE = 'LLM Load is at maximum capacity right now, retry in 30 seconds';

	let config: FailoverQueueConfig = {
		max_queue_length: 10,
		poll_interval_seconds: 2.0,
		full_message: DEFAULT_FULL_MESSAGE
	};

	// Last-persisted snapshot. Drives the `dirty` flag and re-hydrates the
	// form after a successful save (the backend returns the canonical form,
	// so we always reset to that to avoid drift).
	let saved: FailoverQueueConfig = { ...config };
	let loaded = false;
	let saving = false;

	// True when any field differs from what the backend currently has.
	// `Number()` coerces both null (cleared input) and undefined to a
	// comparable numeric value; same trick keeps NaN handling consistent.
	$: dirty =
		(loaded && (Number(config.max_queue_length) || 0) !== (Number(saved.max_queue_length) || 0)) ||
		(loaded &&
			(Number(config.poll_interval_seconds) || 0) !== (Number(saved.poll_interval_seconds) || 0)) ||
		(loaded && (config.full_message ?? '') !== (saved.full_message ?? ''));

	async function save() {
		if (!dirty || saving) return;
		saving = true;
		try {
			const result = await setFailoverQueueConfig(localStorage.token, {
				max_queue_length: Number(config.max_queue_length) || 0,
				poll_interval_seconds: Number(config.poll_interval_seconds) || 0,
				full_message: config.full_message ?? ''
			});
			saved = { ...result };
			config = { ...result };
			toast.success($i18n.t('Failover queue settings saved'));
		} catch (e) {
			console.error('Failed to save failover queue config:', e);
			toast.error($i18n.t('Failed to save failover queue settings'));
		} finally {
			saving = false;
		}
	}

	onMount(async () => {
		try {
			const result = await getFailoverQueueConfig(localStorage.token);
			saved = { ...result };
			config = { ...result };
		} catch (e) {
			console.error('Failed to load failover queue config:', e);
			toast.error($i18n.t('Failed to load failover queue settings'));
		}
		loaded = true;
	});

	const numberInputClass =
		'w-28 h-7 rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 text-xs text-gray-700 outline-hidden transition-colors placeholder:text-gray-300 focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:placeholder:text-gray-700 dark:focus:border-blue-500';
</script>

<form class="flex h-full flex-col justify-between text-sm" on:submit|preventDefault={save}>
	<div class="flex items-center gap-2 mb-4">
		<h2 class="text-sm font-medium text-gray-900 dark:text-white">
			{$i18n.t('failover_queue.title')}
		</h2>
		<Tooltip
			content={$i18n.t(
				'Wrapper-model failover chain: when every provider is at its max_concurrent limit, requests wait in this queue instead of failing.'
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
		{#if !loaded}
			<div class="flex justify-center py-6">
				<Spinner />
			</div>
		{:else}
			<AdminSettingSection title={$i18n.t('failover_queue.section_queue')} first>
				<p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
					{$i18n.t('failover_queue.description')}
				</p>

				<AdminSettingField
					label={$i18n.t('failover_queue.max_queue_length')}
					description={$i18n.t('failover_queue.max_queue_length_description')}
				>
					<input
						class={numberInputClass}
						type="number"
						step="1"
						min="1"
						bind:value={config.max_queue_length}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('failover_queue.poll_interval_seconds')}
					description={$i18n.t('failover_queue.poll_interval_seconds_description')}
				>
					<input
						class={numberInputClass}
						type="number"
						step="0.1"
						min="0.1"
						bind:value={config.poll_interval_seconds}
					/>
				</AdminSettingField>

				<AdminSettingField
					label={$i18n.t('failover_queue.full_message')}
					description={$i18n.t('failover_queue.full_message_description')}
				>
					<Textarea
						bind:value={config.full_message}
						placeholder={DEFAULT_FULL_MESSAGE}
						rows={2}
						className="w-full rounded-lg px-3.5 py-2 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden border border-gray-100/50 dark:border-white/[0.04] focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
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
			disabled={!loaded || saving || !dirty}
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
