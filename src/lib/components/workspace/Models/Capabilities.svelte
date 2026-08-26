<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import Checkbox from '$lib/components/common/Checkbox.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { marked } from 'marked';

	const i18n: Writable<i18nType> = getContext('i18n');

	const capabilityLabels = {
		vision: {
			label: $i18n.t('Vision'),
			description: $i18n.t('Model accepts image inputs')
		},
		file_upload: {
			label: $i18n.t('File Upload'),
			description: $i18n.t('Model accepts file inputs')
		},
		file_context: {
			label: $i18n.t('File Context'),
			description: $i18n.t('Inject file content into conversation context')
		},
		web_search: {
			label: $i18n.t('Web Search'),
			description: $i18n.t('Model can search the web for information')
		},
		image_generation: {
			label: $i18n.t('Image Generation'),
			description: $i18n.t('Model can generate images based on text prompts')
		},
		code_interpreter: {
			label: $i18n.t('Code Interpreter'),
			description: $i18n.t('Model can execute code and perform calculations')
		},
		terminal: {
			label: $i18n.t('Terminal'),
			description: $i18n.t(
				'Model can access Open Terminal for command execution and file management'
			)
		},
		usage: {
			label: $i18n.t('Usage'),
			description: $i18n.t(
				'Sends `stream_options: { include_usage: true }` in the request.\nSupported providers will return token usage information in the response when set.'
			)
		},
		citations: {
			label: $i18n.t('Citations'),
			description: $i18n.t('Displays citations in the response')
		},
		status_updates: {
			label: $i18n.t('Status Updates'),
			description: $i18n.t('Displays status updates (e.g., web search progress) in the response')
		},
		memory: {
			label: $i18n.t('Memory'),
			description: $i18n.t('Inject stored memories into conversation context')
		},
		builtin_tools: {
			label: $i18n.t('Builtin Tools'),
			description: $i18n.t(
				'Automatically inject system tools in native function calling mode (e.g., timestamps, memory, chat history, notes, etc.)'
			)
		},
		api_tools: {
			label: $i18n.t('API Tools'),
			description: $i18n.t(
				'When enabled, this model exposes its builtin and attached tools (web search, knowledge, time) to API/programmatic callers (no UI session). Off by default. Built-in tool access is restricted to time, knowledge, and web_search for privacy.'
			)
		},
		api_terminal: {
			label: $i18n.t('API Terminal'),
			description: $i18n.t(
				'When enabled, this model can use its attached Terminal tool server when called via the API. Off by default. Use with caution — terminal servers can execute arbitrary commands.'
			)
		}
	};

	type Capability = keyof typeof capabilityLabels;

	export let capabilities: Partial<Record<Capability, boolean>> = {};
	export let baseModelId: string | null = null;

	const setCapability = (capability: Capability, checked: boolean) => {
		capabilities[capability] = checked;
		capabilities = capabilities;
	};

	// Hide file_context when file_upload is disabled.
	// For wrapper models (base_model_id set), hide vision and builtin_tools
	// as these are inherited from the base model.
	$: visibleCapabilities = (Object.keys(capabilityLabels) as Capability[]).filter((cap) => {
		if (cap === 'file_context' && !capabilities.file_upload) {
			return false;
		}
		if (baseModelId && (cap === 'vision' || cap === 'builtin_tools')) {
			return false;
		}
		return true;
	});
</script>

<div>
	<div class="mb-1.5 text-xs text-gray-400 dark:text-gray-600">{$i18n.t('Capabilities')}</div>
	{#if baseModelId}
		<div class="mb-2 text-xs text-gray-500 dark:text-gray-400">
			{$i18n.t('Vision and Builtin Tools capabilities are inherited from the base model and cannot be overridden on a wrapper model.')}
		</div>
	{/if}
	<div class="grid grid-cols-1 gap-x-5 gap-y-1 sm:grid-cols-2 lg:grid-cols-3">
		{#each visibleCapabilities as capability}
			<div class="flex min-h-6 items-center gap-2.5">
				<Checkbox
					ariaLabel={$i18n.t(capabilityLabels[capability].label)}
					state={capabilities[capability] ? 'checked' : 'unchecked'}
					on:change={(e) => {
						setCapability(capability, e.detail === 'checked');
					}}
				/>
				<button
					type="button"
					class="min-w-0 cursor-pointer text-left text-xs text-gray-600 dark:text-gray-400"
					on:click={() => setCapability(capability, !capabilities[capability])}
				>
					<Tooltip
						as="span"
						className="block min-w-0"
						content={marked.parse(capabilityLabels[capability].description)}
					>
						<span class="block truncate">{$i18n.t(capabilityLabels[capability].label)}</span>
					</Tooltip>
				</button>
			</div>
		{/each}
	</div>
</div>
