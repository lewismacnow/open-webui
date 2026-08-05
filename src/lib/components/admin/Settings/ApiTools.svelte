<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { getApiToolsConfig, setApiToolsConfig } from '$lib/apis/configs';

	import Switch from '$lib/components/common/Switch.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	const BUILTIN_TOOL_CATEGORIES = [
		{ id: 'time', label: 'Time & Calculation', description: 'Clock, timestamps, date math' },
		{ id: 'knowledge', label: 'Knowledge Base', description: 'Search model-attached knowledge bases' },
		{ id: 'web_search', label: 'Web Search', description: 'Search the web and fetch URLs' },
		{ id: 'image_generation', label: 'Image Generation', description: 'Generate and edit images' },
		{ id: 'code_interpreter', label: 'Code Interpreter', description: 'Execute Python code in a sandbox' },
		{ id: 'chats', label: 'Chat History', description: 'Search past conversations (contains personal data)' },
		{ id: 'memory', label: 'Memory', description: 'Read/write user memories (contains personal data)' },
		{ id: 'notes', label: 'Notes', description: 'Create and read notes (contains personal data)' },
		{ id: 'channels', label: 'Channels', description: 'Post to and search channels (contains personal data)' },
		{ id: 'tasks', label: 'Tasks', description: 'Manage tasks on the chat' },
		{ id: 'automations', label: 'Automations', description: 'Trigger automations' },
		{ id: 'calendar', label: 'Calendar', description: 'View and create calendar events (contains personal data)' },
		{ id: 'subagents', label: 'Sub Agents', description: 'Spawn background AI agents (can incur costs)' },
		{ id: 'skills', label: 'Skills', description: 'Execute configured skills' }
	];

	let enabled = false;
	let allowedCategories: string[] = ['time', 'knowledge', 'web_search'];
	let allowToolServers = false;
	let loaded = false;

	function isAllowed(id: string): boolean {
		return allowedCategories.includes(id);
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
		} catch (e) {
			// Error is logged in the API client
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
		loaded = true;
	});
</script>

<form
	class="flex h-full flex-col justify-between text-sm"
	on:submit|preventDefault={() => {}}
>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('API Tools')}
	</h2>

	<AdminSettingField
		label={$i18n.t('Enable API Tools (Master Switch)')}
		description={$i18n.t(
			'Master switch for the entire API Tools feature. When enabled, models with the API Tools capability can execute tools server-side when called via the API. When disabled, API callers never receive tools regardless of individual settings below.'
		)}
	>
		<Switch
			state={enabled}
			on:change={(e) => {
				enabled = e.detail.state ?? false;
				save();
			}}
		/>
	</AdminSettingField>

	{#if !enabled}
		<div class="flex items-center gap-2 p-3 my-3 rounded-lg bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800/50">
			<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" class="size-4 text-amber-600 dark:text-amber-400 shrink-0">
				<path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
			</svg>
			<span class="text-xs text-amber-700 dark:text-amber-300">
				{$i18n.t('API Tools is disabled. Toggle the master switch above to activate tool calling for API clients.')}
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
				<Switch
					state={allowToolServers}
					on:change={async () => {
						allowToolServers = !allowToolServers;
						await save();
					}}
				/>
			</AdminSettingField>
		</AdminSettingSection>
	</div>
</form>
