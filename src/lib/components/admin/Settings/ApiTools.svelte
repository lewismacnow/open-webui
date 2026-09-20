<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { getApiToolsConfig, setApiToolsConfig } from '$lib/apis/configs';
	import { getBaseModels, updateModelById } from '$lib/apis/models';
	import { toast } from 'svelte-sonner';

	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	const BUILTIN_TOOL_CATEGORIES = [
		{ id: 'time', label: 'Time & Calculation', description: 'Clock, timestamps, date math' },
		{
			id: 'knowledge',
			label: 'Knowledge Base',
			description: 'Search model-attached knowledge bases'
		},
		{ id: 'web_search', label: 'Web Search', description: 'Search the web and fetch URLs' },
		{
			id: 'ask_user',
			label: 'Ask User (Interactive Questions)',
			description:
				'Required for the ask_user Pattern B continuation flow over the API — without this enabled, stateless API clients cannot resume after a clarification prompt.'
		},
		{ id: 'image_generation', label: 'Image Generation', description: 'Generate and edit images' },
		{
			id: 'code_interpreter',
			label: 'Code Interpreter',
			description: 'Execute Python code in a sandbox'
		},
		{
			id: 'chats',
			label: 'Chat History',
			description: 'Search past conversations (contains personal data)'
		},
		{
			id: 'memory',
			label: 'Memory',
			description: 'Read/write user memories (contains personal data)'
		},
		{ id: 'notes', label: 'Notes', description: 'Create and read notes (contains personal data)' },
		{
			id: 'channels',
			label: 'Channels',
			description: 'Post to and search channels (contains personal data)'
		},
		{ id: 'tasks', label: 'Tasks', description: 'Manage tasks on the chat' },
		{ id: 'automations', label: 'Automations', description: 'Trigger automations' },
		{
			id: 'calendar',
			label: 'Calendar',
			description: 'View and create calendar events (contains personal data)'
		},
		{
			id: 'subagents',
			label: 'Sub Agents',
			description: 'Spawn background AI agents (can incur costs)'
		},
		{ id: 'skills', label: 'Skills', description: 'Execute configured skills' }
	];

	let enabled = false;
	let allowedCategories: string[] = ['time', 'knowledge', 'web_search'];
	let allowToolServers = false;
	let loaded = false;

	// Per-model api_tools capability — required for any model to participate
	// in API Tools mode. The global config (above) gates the feature; this
	// section decides WHICH MODELS opt in. Without a model listed here as
	// "API Tools: on", api_tools_active stays False and the wrapper never
	// runs regardless of the master switch.
	let models: any[] = [];
	let modelsLoading = false;
	let savingModelId: string | null = null;

	function isAllowed(id: string): boolean {
		return allowedCategories.includes(id);
	}

	function modelHasApiTools(model: any): boolean {
		return !!model?.info?.meta?.capabilities?.api_tools;
	}

	async function toggleCategory(id: string, value: boolean) {
		if (value) {
			if (!allowedCategories.includes(id)) {
				allowedCategories = [...allowedCategories, id];
			}
		} else {
			allowedCategories = allowedCategories.filter((c) => c !== id);
		}
		await save();
	}

	async function save() {
		try {
			await setApiToolsConfig(localStorage.token, {
				enabled: enabled,
				allowed_categories: allowedCategories,
				allow_tool_servers: allowToolServers
			});
			toast.success($i18n.t('API Tools settings saved'));
		} catch (e) {
			toast.error($i18n.t('Failed to save API Tools settings'));
		}
	}

	async function toggleModelApiTools(model: any) {
		const wasOn = modelHasApiTools(model);
		savingModelId = model.id;
		try {
			const meta = JSON.parse(JSON.stringify(model?.info?.meta || {}));
			const capabilities = JSON.parse(JSON.stringify(meta.capabilities || {}));
			capabilities.api_tools = !wasOn;
			// api_tools implicitly requires builtin_tools to be on; if the model
			// has it explicitly disabled, don't silently override — let the admin
			// fix it via the model editor.
			capabilities.builtin_tools = capabilities.builtin_tools ?? true;
			meta.capabilities = capabilities;

			await updateModelById(localStorage.token, model.id, {
				...model,
				info: { ...model.info, meta }
			});

			// Update local state so the switch reflects the change immediately.
			model.info.meta.capabilities = capabilities;
			models = [...models];

			toast.success(
				wasOn
					? $i18n.t('API Tools disabled for {name}', { name: model.name })
					: $i18n.t('API Tools enabled for {name}', { name: model.name })
			);
		} catch (e) {
			console.error('Failed to toggle API Tools for model:', e);
			toast.error($i18n.t('Failed to update model API Tools capability'));
		} finally {
			savingModelId = null;
		}
	}

	onMount(async () => {
		try {
			const config = await getApiToolsConfig(localStorage.token);
			enabled = config.enabled;
			allowedCategories = config.allowed_categories;
			allowToolServers = config.allow_tool_servers;
		} catch (e) {
			console.error('Failed to load API tools config:', e);
		}

		modelsLoading = true;
		try {
			models = (await getBaseModels(localStorage.token)) ?? [];
		} catch (e) {
			console.error('Failed to load models for API Tools capability toggle:', e);
		} finally {
			modelsLoading = false;
		}

		loaded = true;
	});
</script>

<form class="flex h-full flex-col justify-between text-sm" on:submit|preventDefault={() => {}}>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('API Tools')}
	</h2>

	<AdminSettingField
		label={$i18n.t('Enable API Tools (Master Switch)')}
		description={$i18n.t(
			'Master switch for the entire API Tools feature. When enabled, models with the API Tools capability can execute tools server-side when called via the API. When disabled, API callers never receive tools regardless of individual settings below.'
		)}
	>
		<Switch bind:state={enabled} on:change={save} />
	</AdminSettingField>

	{#if !enabled}
		<div
			class="flex items-center gap-2 p-3 my-3 rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800/50"
		>
			<svg
				xmlns="http://www.w3.org/2000/svg"
				fill="none"
				viewBox="0 0 24 24"
				stroke-width="2"
				stroke="currentColor"
				class="size-4 text-amber-600 dark:text-amber-400 shrink-0"
			>
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"
				/>
			</svg>
			<span class="text-xs text-amber-700 dark:text-amber-300">
				{$i18n.t(
					'API Tools is disabled. Toggle the master switch above to activate tool calling for API clients.'
				)}
			</span>
		</div>
	{/if}

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5" class:opacity-50={!enabled}>
		<AdminSettingSection title={$i18n.t('API Tools Policy')} first>
			<p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
				{$i18n.t(
					'These settings control which tools are available to external API callers when a model has the API Tools capability enabled. UI callers always have access to all configured tools. Changes take effect immediately.'
				)}
			</p>

			<!-- Builtin tools grid -->
			<div class="grid grid-cols-1 md:grid-cols-2 gap-3 my-4">
				{#each BUILTIN_TOOL_CATEGORIES as category}
					<div
						class="flex items-center justify-between gap-3 p-3 rounded-xl border border-gray-100 dark:border-gray-850"
					>
						<div class="flex-1 min-w-0">
							<div class="flex items-center gap-1.5">
								<span class="text-sm font-medium">{$i18n.t(category.label)}</span>
								{#if category.description.includes('personal data')}
									<Tooltip
										content={$i18n.t('May contain personal or sensitive data')}
										placement="top"
									>
										<svg
											xmlns="http://www.w3.org/2000/svg"
											viewBox="0 0 16 16"
											fill="currentColor"
											class="size-3.5 shrink-0 text-amber-500"
										>
											<path
												fill-rule="evenodd"
												d="M8 15A7 7 0 1 0 8 1a7 7 0 0 0 0 14ZM8.75 4.25a.75.75 0 0 0-1.5 0v4.5a.75.75 0 0 0 1.5 0v-4.5Zm-.75 7.5a.75.75 0 0 0 0 1.5h.007a.75.75 0 0 0 0-1.5H8Z"
												clip-rule="evenodd"
											/>
										</svg>
									</Tooltip>
								{/if}
							</div>
							<p class="text-xs text-gray-400">{$i18n.t(category.description)}</p>
						</div>
						<Switch
							state={isAllowed(category.id)}
							on:change={(e) => toggleCategory(category.id, e.detail)}
						/>
					</div>
				{/each}
			</div>

			<!-- Tool servers toggle -->
			<AdminSettingField
				label={$i18n.t('Allow External Tool Servers via API')}
				description={$i18n.t(
					'When enabled, MCP tool servers and OpenAPI tool servers attached to a model become available to API callers. When disabled, only the builtin tools selected above are available. Direct client-side tool servers never work via API (they require a browser socket connection).'
				)}
			>
				<Switch bind:state={allowToolServers} on:change={save} />
			</AdminSettingField>
		</AdminSettingSection>

		<AdminSettingSection title={$i18n.t('Per-Model API Tools Capability')}>
			<p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
				{$i18n.t(
					'Each model must explicitly opt in to API Tools before the wrapper will execute tools for it. Toggle a model on to allow it to receive and execute builtin tools server-side when called via the API. The model also needs the builtin_tools capability, which is enabled by default. The global master switch above must be on for these per-model toggles to take effect.'
				)}
			</p>

			{#if modelsLoading}
				<div class="flex items-center gap-2 py-4 text-sm text-gray-500">
					<Spinner className="size-4" />
					{$i18n.t('Loading models...')}
				</div>
			{:else if models.length === 0}
				<div
					class="flex items-center gap-2 p-3 my-3 rounded-lg bg-gray-50 dark:bg-gray-950/30 border border-gray-200 dark:border-gray-800/50"
				>
					<span class="text-xs text-gray-500 dark:text-gray-400">
						{$i18n.t('No models found. Create a workspace model first to enable API Tools on it.')}
					</span>
				</div>
			{:else}
				<div class="space-y-2 my-4">
					{#each models as model (model.id)}
						<div
							class="flex items-center justify-between gap-3 p-3 rounded-xl border border-gray-100 dark:border-gray-850"
						>
							<div class="flex-1 min-w-0">
								<div class="text-sm font-medium truncate">
									{model.name || model.id}
								</div>
								<p class="text-xs text-gray-400 truncate">
									{model.id}
									{#if !modelHasApiTools(model)}
										<span class="ml-2 text-amber-500">
											· {$i18n.t('API Tools: off')}
										</span>
									{:else}
										<span class="ml-2 text-green-500">
											· {$i18n.t('API Tools: on')}
										</span>
									{/if}
								</p>
							</div>
							{#if savingModelId === model.id}
								<Spinner className="size-4" />
							{:else}
								<Switch
									state={modelHasApiTools(model)}
									on:change={() => toggleModelApiTools(model)}
								/>
							{/if}
						</div>
					{/each}
				</div>
			{/if}
		</AdminSettingSection>
	</div>

	<div class="flex justify-end pt-3 pb-3">
		<button
			type="button"
			class="px-3.5 py-1.5 text-sm font-medium bg-black dark:bg-white text-white dark:text-black rounded-lg transition"
			on:click={save}
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
