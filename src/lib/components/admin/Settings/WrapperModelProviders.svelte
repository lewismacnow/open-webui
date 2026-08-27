<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { models } from '$lib/stores';
	import { toast } from 'svelte-sonner';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import {
		getWrapperProviderChains,
		setWrapperProviderChains,
		type WrapperProviderChains
	} from '$lib/apis/configs';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import AdminSettingField from './AdminSettingField.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	// The single global chain. One ordered list of providers applied to
	// every wrapper model with failover_source='global'; the first entry
	// is the primary.
	let chain: WrapperProviderChains = [];
	let loaded = false;
	let saving = false;

	// Debounced autosave handle (ApiTools-style change-driven save, with a
	// short debounce because chain edits fire many change events).
	let saveTimer: ReturnType<typeof setTimeout> | null = null;

	// RAG relevance threshold (DB config key 'rag.relevance_threshold').
	// Local copy + last-saved copy so the dirty check can skip no-op saves.
	let relevanceThreshold: number = 0;
	let savedThreshold: number = 0;
	let thresholdSaveTimer: ReturnType<typeof setTimeout> | null = null;

	// Provider candidates: non-preset, non-arena, non-direct (same filter
	// as FailoverProviders.svelte). No wrapper exclusion — the chain is
	// global and never keyed by a wrapper model.
	$: providerModels = ($models ?? []).filter(
		(m: any) => !m?.preset && m?.owned_by !== 'arena' && !(m?.direct ?? false)
	);

	// Chain model ids that no longer appear in $models — e.g. their
	// connection was disabled. They stay listed (marked "missing") so an
	// admin can intentionally keep offline providers in the chain for
	// failover testing or migration scenarios.
	$: missingChainModelIds = Array.from(
		new Set(
			chain
				.map((p) => p.model_id)
				.filter((id) => id && !providerModels.some((m: any) => m.id === id))
		)
	);

	function scheduleSave() {
		if (!loaded) return;
		if (saveTimer) clearTimeout(saveTimer);
		saveTimer = setTimeout(() => {
			saveTimer = null;
			save();
		}, 800);
	}

	// Drop half-configured rows before persisting: entries without a
	// model are kept client-side only, mirroring the failover map's
	// "absence = unset" convention.
	function sanitizedChain(): WrapperProviderChains {
		return chain
			.filter((p) => p.model_id)
			.map((p) => ({
				model_id: p.model_id,
				max_concurrent: p.max_concurrent ?? null
			}));
	}

	async function save() {
		if (saveTimer) {
			clearTimeout(saveTimer);
			saveTimer = null;
		}
		saving = true;
		try {
			await setWrapperProviderChains(localStorage.token, sanitizedChain());
			toast.success($i18n.t('Wrapper model providers saved'));
		} catch (e) {
			toast.error($i18n.t('Failed to save wrapper model providers'));
		} finally {
			saving = false;
		}
	}

	function addProvider() {
		chain = [...chain, { model_id: '', max_concurrent: null }];
	}

	function removeProvider(idx: number) {
		chain = chain.filter((_, i) => i !== idx);
		scheduleSave();
	}

	function moveProvider(idx: number, direction: number) {
		const newIdx = idx + direction;
		if (newIdx < 0 || newIdx >= chain.length) return;
		const copy = [...chain];
		[copy[idx], copy[newIdx]] = [copy[newIdx], copy[idx]];
		chain = copy;
		scheduleSave();
	}

	// Empty input = unlimited (null).
	function setMaxConcurrent(idx: number, raw: string) {
		if (!chain[idx]) return;
		const parsed = raw === '' ? null : Math.max(0, parseInt(raw, 10));
		chain = chain.map((entry, i) =>
			i === idx
				? {
						...entry,
						max_concurrent: parsed !== null && Number.isNaN(parsed) ? null : parsed
					}
				: entry
		);
		scheduleSave();
	}

	// --- RAG relevance threshold (DB config key 'rag.relevance_threshold') ---

	// Read the persisted threshold from the rag config namespace. Missing
	// or invalid values fall back to 0 (soft filter off).
	async function fetchRelevanceThreshold(token: string): Promise<number> {
		const res = await fetch(`${WEBUI_API_BASE_URL}/configs/namespace/rag`, {
			headers: {
				Authorization: `Bearer ${token}`
			}
		});
		if (!res.ok) throw await res.json();
		const config = await res.json();
		const raw = config?.['rag.relevance_threshold'];
		const parsed = typeof raw === 'string' ? parseFloat(raw) : Number(raw);
		return Number.isFinite(parsed) ? Math.min(1, Math.max(0, parsed)) : 0;
	}

	// Persist via the generic config import endpoint (admin-only; upserts
	// only the provided key, leaving every other DB config entry intact).
	async function persistRelevanceThreshold(token: string, value: number): Promise<void> {
		const res = await fetch(`${WEBUI_API_BASE_URL}/configs/import`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${token}`
			},
			body: JSON.stringify({ config: { 'rag.relevance_threshold': value } })
		});
		if (!res.ok) throw await res.json();
	}

	function scheduleThresholdSave() {
		if (!loaded) return;
		if (thresholdSaveTimer) clearTimeout(thresholdSaveTimer);
		thresholdSaveTimer = setTimeout(() => {
			thresholdSaveTimer = null;
			saveThreshold();
		}, 800);
	}

	async function saveThreshold() {
		if (thresholdSaveTimer) {
			clearTimeout(thresholdSaveTimer);
			thresholdSaveTimer = null;
		}
		if (!loaded) return;
		// Clamp into [0, 1]; an empty/invalid input counts as 0.
		const value = Math.min(1, Math.max(0, Number(relevanceThreshold) || 0));
		relevanceThreshold = value;
		if (value === savedThreshold) return;
		try {
			await persistRelevanceThreshold(localStorage.token, value);
			savedThreshold = value;
			toast.success($i18n.t('RAG relevance threshold saved'));
		} catch (e) {
			toast.error($i18n.t('Failed to save RAG relevance threshold'));
		}
	}

	onMount(async () => {
		try {
			chain = await getWrapperProviderChains(localStorage.token);
		} catch (e) {
			console.error('Failed to load wrapper provider chain:', e);
			toast.error($i18n.t('Failed to load wrapper model providers'));
		}

		try {
			relevanceThreshold = await fetchRelevanceThreshold(localStorage.token);
			savedThreshold = relevanceThreshold;
		} catch (e) {
			console.error('Failed to load RAG relevance threshold:', e);
		}
		loaded = true;
	});
</script>

<form class="flex h-full flex-col justify-between text-sm" on:submit|preventDefault={() => {}}>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('Wrapper Model Providers')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		<AdminSettingSection title={$i18n.t('RAG')} first>
			<AdminSettingField
				label={$i18n.t('RAG relevance threshold')}
				description={$i18n.t(
					'Soft filter for RAG results. 0 = return everything (default). Higher values drop low-similarity docs from the top-k window. Range: 0.0–1.0.'
				)}
			>
				<input
					type="number"
					step="0.01"
					min="0"
					max="1"
					class="w-28 text-sm bg-transparent outline-none border border-gray-200 dark:border-gray-700 rounded px-1.5 py-0.5"
					bind:value={relevanceThreshold}
					on:change={scheduleThresholdSave}
					on:blur={saveThreshold}
				/>
			</AdminSettingField>
		</AdminSettingSection>

		<AdminSettingSection title={$i18n.t('Global Provider Chain')}>
			<p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
				{$i18n.t(
					'Global provider chain — applies to every wrapper model set to use the global chain (configured per-wrapper via the model editor).'
				)}
			</p>

			{#if !loaded}
				<div class="flex justify-center py-6">
					<Spinner />
				</div>
			{:else if chain.length === 0}
				<div class="text-xs text-gray-400 dark:text-gray-500 py-4">
					{$i18n.t(
						'No providers in the global chain. Add providers to enable automatic failover for all wrapper models.'
					)}
				</div>
			{:else}
				<div class="flex flex-col gap-1.5 my-2">
					{#each chain as entry, idx (idx)}
						<div
							class="rounded-lg border border-gray-200 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-850/50 px-3 py-2"
						>
							<div class="flex items-center gap-2">
								<!-- Position label -->
								<div
									class="shrink-0 flex items-center gap-1.5 w-24 text-xs font-medium uppercase tracking-wide {idx ===
									0
										? 'text-gray-700 dark:text-gray-300'
										: 'text-gray-500 dark:text-gray-400'}"
								>
									{#if idx === 0}
										<span class="w-1.5 h-1.5 rounded-full bg-green-500"></span>
										{$i18n.t('Primary')}
									{:else}
										<span class="w-1.5 h-1.5 rounded-full bg-amber-400/70"></span>
										{$i18n.t('Backup {{n}}', { n: idx })}
									{/if}
								</div>

								<!-- Model selector -->
								<select
									class="flex-1 min-w-0 text-sm bg-transparent outline-none truncate"
									bind:value={entry.model_id}
									aria-label={$i18n.t('Model')}
									on:change={scheduleSave}
								>
									<option value="" class="text-gray-900">
										{$i18n.t('Select a model')}
									</option>
									{#each missingChainModelIds as id (id)}
										<option value={id} class="text-gray-900">
											{id} ({$i18n.t('missing')})
										</option>
									{/each}
									{#each providerModels as m (m.id)}
										<option value={m.id} class="text-gray-900">{m.name}</option>
									{/each}
								</select>

								<!-- Max concurrent limit (empty = unlimited) -->
								<div class="flex items-center gap-1 shrink-0">
									<input
										type="number"
										min="0"
										class="w-16 text-xs text-right bg-transparent outline-none border border-gray-200 dark:border-gray-700 rounded px-1.5 py-0.5"
										placeholder={$i18n.t('Unlimited')}
										value={entry.max_concurrent ?? ''}
										on:change={(e) =>
											setMaxConcurrent(idx, (e.currentTarget as HTMLInputElement).value)}
										aria-label={$i18n.t('Max concurrent')}
										title={$i18n.t(
											'Maximum concurrent requests before trying the next provider. Leave empty for unlimited.'
										)}
									/>
								</div>

								<!-- Reorder / remove -->
								<div class="flex items-center shrink-0 text-gray-400">
									<button
										type="button"
										class="p-1 hover:text-black dark:hover:text-white disabled:opacity-20 disabled:pointer-events-none"
										on:click={() => moveProvider(idx, -1)}
										disabled={idx === 0}
										aria-label={$i18n.t('Move up')}
										title={$i18n.t('Move up')}
									>
										<svg viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
											<path
												fill-rule="evenodd"
												d="M10 17a.75.75 0 01-.75-.75V5.612L5.29 9.77a.75.75 0 01-1.08-1.04l5.25-5.5a.75.75 0 011.08 0l5.25 5.5a.75.75 0 11-1.08 1.04L10.75 5.612V16.25A.75.75 0 0110 17z"
												clip-rule="evenodd"
											/>
										</svg>
									</button>
									<button
										type="button"
										class="p-1 hover:text-black dark:hover:text-white disabled:opacity-20 disabled:pointer-events-none"
										on:click={() => moveProvider(idx, 1)}
										disabled={idx === chain.length - 1}
										aria-label={$i18n.t('Move down')}
										title={$i18n.t('Move down')}
									>
										<svg viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
											<path
												fill-rule="evenodd"
												d="M10 3a.75.75 0 01.75.75v10.638l3.96-4.158a.75.75 0 111.08 1.04l-5.25 5.5a.75.75 0 01-1.08 0l-5.25-5.5a.75.75 0 111.08-1.04l3.96 4.158V3.75A.75.75 0 0110 3z"
												clip-rule="evenodd"
											/>
										</svg>
									</button>
									<button
										type="button"
										class="p-1 hover:text-red-500"
										on:click={() => removeProvider(idx)}
										aria-label={$i18n.t('Remove provider')}
										title={$i18n.t('Remove provider')}
									>
										<svg viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
											<path
												d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
											/>
										</svg>
									</button>
								</div>
							</div>
						</div>
					{/each}
				</div>
			{/if}

			<button
				type="button"
				on:click={addProvider}
				class="w-full text-sm border border-dashed border-gray-300 dark:border-gray-700 rounded-lg py-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:border-gray-400 dark:hover:border-gray-600 transition"
			>
				+ {$i18n.t('Add provider')}
			</button>
		</AdminSettingSection>
	</div>

	<div class="flex justify-end pt-3 pb-3">
		<button
			type="button"
			class="px-3.5 py-1.5 text-sm font-medium bg-black dark:bg-white text-white dark:text-black rounded-lg transition disabled:opacity-50"
			on:click={() => {
				save();
				saveThreshold();
			}}
			disabled={!loaded || saving}
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
