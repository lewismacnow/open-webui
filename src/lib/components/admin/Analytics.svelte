<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { user } from '$lib/stores';
	import {
		getApiKeyTokenUsage,
		getEndpointTokenUsage,
		type ApiKeyTokenUsageResponse,
		type EndpointTokenUsageResponse
	} from '$lib/apis/configs';

	import Dashboard from './Analytics/Dashboard.svelte';

	const i18n: any = getContext('i18n');

	let loaded = false;
	let apiKeyUsage: ApiKeyTokenUsageResponse | null = null;
	let endpointUsage: EndpointTokenUsageResponse | null = null;

	// API-path token usage (`api_token_usage` table) — a separate data
	// source from the chat analytics dashboard above (which reads
	// `chat_message.usage`). Loaded all-time; the dashboard's date
	// filters do not apply to these sections.
	async function loadApiTokenUsage() {
		try {
			apiKeyUsage = await getApiKeyTokenUsage(localStorage.token, 50);
		} catch (e) {
			console.error('Failed to load API key token usage:', e);
		}
		try {
			endpointUsage = await getEndpointTokenUsage(localStorage.token);
		} catch (e) {
			console.error('Failed to load endpoint token usage:', e);
		}
	}

	// Backend sorts by total tokens, but sort client-side too so the
	// ordering survives any future response-shape drift.
	$: sortedApiKeyUsage = apiKeyUsage
		? [...apiKeyUsage.keys].sort((a, b) => (b.total_tokens ?? 0) - (a.total_tokens ?? 0))
		: [];
	$: sortedEndpointUsage = endpointUsage
		? [...endpointUsage.endpoints].sort((a, b) => (b.total_tokens ?? 0) - (a.total_tokens ?? 0))
		: [];

	const fmt = (n: number | null | undefined) => (n ?? 0).toLocaleString();

	onMount(async () => {
		if ($user?.role !== 'admin') {
			await goto('/');
		}
		await loadApiTokenUsage();
		loaded = true;
	});
</script>

{#if loaded}
	<div class="w-full h-full pb-2">
		<Dashboard />

		<!-- API token usage (OpenAI-compatible path; not part of the chat
		     analytics above). Mirrors the Usage section on Admin → Token
		     Caps for admins who live on this page. -->
		<div class="px-4 mt-4 space-y-4">
			<div class="flex items-center justify-between">
				<h3 class="text-xs text-gray-400 dark:text-gray-600">
					{$i18n.t('API Token Usage')}
				</h3>
				<button
					type="button"
					class="text-xs text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
					on:click={loadApiTokenUsage}
				>
					{$i18n.t('Refresh')}
				</button>
			</div>

			<!-- API token usage by key -->
			<div>
				<h4 class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-2">
					{$i18n.t('API token usage by key')}
				</h4>

				{#if apiKeyUsage === null}
					<div class="text-xs text-gray-400 dark:text-gray-500 py-2">{$i18n.t('Loading...')}</div>
				{:else if sortedApiKeyUsage.length === 0}
					<div class="text-xs text-gray-400 dark:text-gray-500 py-2">
						{$i18n.t('No API token usage recorded yet.')}
					</div>
				{:else}
					<div class="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-800">
						<table class="w-full text-xs">
							<thead class="bg-gray-50 dark:bg-gray-850 text-gray-500 dark:text-gray-400">
								<tr>
									<th class="px-3 py-1.5 text-left font-medium">{$i18n.t('API key')}</th>
									<th class="px-3 py-1.5 text-right font-medium">{$i18n.t('Prompt tokens')}</th>
									<th class="px-3 py-1.5 text-right font-medium">{$i18n.t('Completion tokens')}</th>
									<th class="px-3 py-1.5 text-right font-medium">{$i18n.t('Total tokens')}</th>
									<th class="px-3 py-1.5 text-right font-medium">{$i18n.t('Requests')}</th>
								</tr>
							</thead>
							<tbody class="divide-y divide-gray-100 dark:divide-gray-850">
								{#each sortedApiKeyUsage as k (k.api_key_id)}
									<tr>
										<td class="px-3 py-1.5 font-mono text-[0.6875rem] truncate max-w-48"
											>{k.api_key_id}</td
										>
										<td class="px-3 py-1.5 text-right tabular-nums">{fmt(k.prompt_tokens)}</td>
										<td class="px-3 py-1.5 text-right tabular-nums">{fmt(k.completion_tokens)}</td>
										<td class="px-3 py-1.5 text-right tabular-nums font-medium"
											>{fmt(k.total_tokens)}</td
										>
										<td class="px-3 py-1.5 text-right tabular-nums">{fmt(k.request_count)}</td>
									</tr>
								{/each}
								<tr class="bg-gray-50/70 dark:bg-gray-850/70 font-medium">
									<td class="px-3 py-1.5">{$i18n.t('Total')}</td>
									<td class="px-3 py-1.5 text-right tabular-nums"
										>{fmt(apiKeyUsage.total_prompt_tokens)}</td
									>
									<td class="px-3 py-1.5 text-right tabular-nums"
										>{fmt(apiKeyUsage.total_completion_tokens)}</td
									>
									<td class="px-3 py-1.5 text-right tabular-nums"
										>{fmt(apiKeyUsage.total_tokens)}</td
									>
									<td class="px-3 py-1.5 text-right tabular-nums"
										>{fmt(apiKeyUsage.total_request_count)}</td
									>
								</tr>
							</tbody>
						</table>
					</div>
				{/if}
			</div>

			<!-- UI vs API split -->
			<div>
				<h4 class="text-xs font-medium text-gray-600 dark:text-gray-400 mb-2">
					{$i18n.t('UI vs API split')}
				</h4>

				{#if endpointUsage === null}
					<div class="text-xs text-gray-400 dark:text-gray-500 py-2">{$i18n.t('Loading...')}</div>
				{:else if sortedEndpointUsage.length === 0}
					<div class="text-xs text-gray-400 dark:text-gray-500 py-2">
						{$i18n.t('No API token usage recorded yet.')}
					</div>
				{:else}
					<div class="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-800">
						<table class="w-full text-xs">
							<thead class="bg-gray-50 dark:bg-gray-850 text-gray-500 dark:text-gray-400">
								<tr>
									<th class="px-3 py-1.5 text-left font-medium">{$i18n.t('Endpoint')}</th>
									<th class="px-3 py-1.5 text-right font-medium">{$i18n.t('Prompt tokens')}</th>
									<th class="px-3 py-1.5 text-right font-medium">{$i18n.t('Completion tokens')}</th>
									<th class="px-3 py-1.5 text-right font-medium">{$i18n.t('Total tokens')}</th>
									<th class="px-3 py-1.5 text-right font-medium">{$i18n.t('Requests')}</th>
								</tr>
							</thead>
							<tbody class="divide-y divide-gray-100 dark:divide-gray-850">
								{#each sortedEndpointUsage as e (e.endpoint)}
									<tr>
										<td class="px-3 py-1.5 font-mono text-[0.6875rem]">{e.endpoint}</td>
										<td class="px-3 py-1.5 text-right tabular-nums">{fmt(e.prompt_tokens)}</td>
										<td class="px-3 py-1.5 text-right tabular-nums">{fmt(e.completion_tokens)}</td>
										<td class="px-3 py-1.5 text-right tabular-nums font-medium"
											>{fmt(e.total_tokens)}</td
										>
										<td class="px-3 py-1.5 text-right tabular-nums">{fmt(e.request_count)}</td>
									</tr>
								{/each}
								<tr class="bg-gray-50/70 dark:bg-gray-850/70 font-medium">
									<td class="px-3 py-1.5">{$i18n.t('Total')}</td>
									<td class="px-3 py-1.5 text-right tabular-nums"
										>{fmt(endpointUsage.total_prompt_tokens)}</td
									>
									<td class="px-3 py-1.5 text-right tabular-nums"
										>{fmt(endpointUsage.total_completion_tokens)}</td
									>
									<td class="px-3 py-1.5 text-right tabular-nums"
										>{fmt(endpointUsage.total_tokens)}</td
									>
									<td class="px-3 py-1.5 text-right tabular-nums"
										>{fmt(endpointUsage.total_request_count)}</td
									>
								</tr>
							</tbody>
						</table>
					</div>
				{/if}
			</div>
		</div>
	</div>
{/if}
