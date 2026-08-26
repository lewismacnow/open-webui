<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { models } from '$lib/stores';
	import { toast } from 'svelte-sonner';
	import {
		getWrapperProviderChains,
		setWrapperProviderChains,
		type WrapperProviderChains
	} from '$lib/apis/configs';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import AdminSettingSection from './AdminSettingSection.svelte';

	const i18n: any = getContext('i18n');

	// Global chains keyed by wrapper model id. Each chain is an ordered
	// list of providers; the first entry is the primary.
	let chains: WrapperProviderChains = {};
	let loaded = false;
	let saving = false;

	// Debounced autosave handle (ApiTools-style change-driven save, with a
	// short debounce because chain edits fire many change events).
	let saveTimer: ReturnType<typeof setTimeout> | null = null;

	// Wrapper models = workspace presets (same exclusion style as
	// FailoverProviders.svelte uses, but keeping presets since wrapper
	// models ARE presets).
	$: wrapperModels = ($models ?? []).filter(
		(m: any) => m?.preset && m?.owned_by !== 'arena' && !(m?.direct ?? false)
	);

	// Provider candidates for a chain's rows: non-preset, non-arena,
	// non-direct, excluding the chain's own wrapper model so a chain
	// can't self-reference (same filter as FailoverProviders.svelte).
	const providerModelsFor = (key: string) =>
		($models ?? []).filter(
			(m: any) =>
				(!key || m.id !== key) && !m?.preset && m?.owned_by !== 'arena' && !(m?.direct ?? false)
		);

	$: sortedChainKeys = Object.keys(chains).sort();

	function scheduleSave() {
		if (!loaded) return;
		if (saveTimer) clearTimeout(saveTimer);
		saveTimer = setTimeout(() => {
			saveTimer = null;
			save();
		}, 800);
	}

	// Drop half-configured state before persisting: chains without a key
	// and entries without a model are kept client-side only, mirroring the
	// failover map's "absence-of-key = unset" convention.
	function sanitizedChains(): WrapperProviderChains {
		const out: WrapperProviderChains = {};
		for (const [key, entries] of Object.entries(chains)) {
			if (!key) continue;
			const valid = (entries ?? []).filter((p) => p.model_id);
			if (valid.length > 0) {
				out[key] = valid.map((p) => ({
					model_id: p.model_id,
					max_concurrent: p.max_concurrent ?? null
				}));
			}
		}
		return out;
	}

	async function save() {
		if (saveTimer) {
			clearTimeout(saveTimer);
			saveTimer = null;
		}
		saving = true;
		try {
			await setWrapperProviderChains(localStorage.token, sanitizedChains());
			toast.success($i18n.t('Wrapper model providers saved'));
		} catch (e) {
			toast.error($i18n.t('Failed to save wrapper model providers'));
		} finally {
			saving = false;
		}
	}

	function addChain() {
		const used = new Set(Object.keys(chains));
		const candidate = wrapperModels.find((m: any) => !used.has(m.id));
		const key = candidate?.id ?? '';
		chains = { ...chains, [key]: [] };
	}

	function deleteChain(key: string) {
		const next = { ...chains };
		delete next[key];
		chains = next;
		scheduleSave();
		toast.success($i18n.t('Chain deleted'));
	}

	// Changing the header select renames the key and moves the entries.
	function renameChainKey(oldKey: string, newKey: string) {
		if (!newKey || newKey === oldKey) return;
		const next = { ...chains };
		next[newKey] = next[oldKey] ?? [];
		delete next[oldKey];
		chains = next;
		scheduleSave();
	}

	function addProvider(key: string) {
		chains = { ...chains, [key]: [...(chains[key] ?? []), { model_id: '', max_concurrent: null }] };
	}

	function removeProvider(key: string, idx: number) {
		chains = { ...chains, [key]: (chains[key] ?? []).filter((_, i) => i !== idx) };
		scheduleSave();
	}

	function moveProvider(key: string, idx: number, direction: number) {
		const entries = chains[key] ?? [];
		const newIdx = idx + direction;
		if (newIdx < 0 || newIdx >= entries.length) return;
		const copy = [...entries];
		[copy[idx], copy[newIdx]] = [copy[newIdx], copy[idx]];
		chains = { ...chains, [key]: copy };
		scheduleSave();
	}

	// Empty input = unlimited (null).
	function setMaxConcurrent(key: string, idx: number, raw: string) {
		const entries = [...(chains[key] ?? [])];
		if (!entries[idx]) return;
		const parsed = raw === '' ? null : Math.max(0, parseInt(raw, 10));
		entries[idx] = {
			...entries[idx],
			max_concurrent: parsed !== null && Number.isNaN(parsed) ? null : parsed
		};
		chains = { ...chains, [key]: entries };
		scheduleSave();
	}

	function modelDisplayName(id: string): string {
		const m = ($models ?? []).find((m: any) => m.id === id);
		return m?.name ?? id;
	}

	onMount(async () => {
		try {
			chains = await getWrapperProviderChains(localStorage.token);
		} catch (e) {
			console.error('Failed to load wrapper provider chains:', e);
			toast.error($i18n.t('Failed to load wrapper model providers'));
		}
		loaded = true;
	});
</script>

<form class="flex h-full flex-col justify-between text-sm" on:submit|preventDefault={() => {}}>
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
		{$i18n.t('Wrapper Model Providers')}
	</h2>

	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5">
		<AdminSettingSection title={$i18n.t('Global Provider Chains')} first>
			<p class="text-sm text-gray-500 dark:text-gray-400 mb-4">
				{$i18n(
					'Global provider chains for wrapper models. Define an ordered list of providers per wrapper model. Applies to every wrapper model set to use the global chain; individual models can override with a custom chain in the workspace model editor.'
				)}
			</p>

			{#if !loaded}
				<div class="flex justify-center py-6">
					<Spinner />
				</div>
			{:else if sortedChainKeys.length === 0}
				<div class="text-xs text-gray-400 dark:text-gray-500 py-4">
					{$i18n.t('No global chains configured yet.')}
				</div>
			{:else}
				<div class="flex flex-col gap-3 my-2">
					{#each sortedChainKeys as key (key)}
						<div class="rounded-xl border border-gray-100 dark:border-gray-850 px-3 py-2">
							<!-- Chain header: wrapper model (chain key) -->
							<div class="flex items-center gap-2">
								<select
									class="flex-1 min-w-0 text-sm font-medium bg-transparent outline-none truncate"
									value={key}
									aria-label={$i18n.t('Wrapper model')}
									on:change={(e) =>
										renameChainKey(key, (e.currentTarget as HTMLSelectElement).value)}
								>
									{#if key === ''}
										<option value="" class="text-gray-900">
											{$i18n.t('Select a wrapper model')}
										</option>
									{/if}
									{#if key && !wrapperModels.find((m: any) => m.id === key)}
										<!-- Preserve an id that no longer appears in the store
										     (e.g. model deleted) so the admin can see it
										     before re-picking. -->
										<option value={key} class="text-gray-900">
											{key} ({$i18n.t('missing')})
										</option>
									{/if}
									{#each wrapperModels as m (m.id)}
										<option
											value={m.id}
											class="text-gray-900"
											disabled={m.id !== key && Object.keys(chains).includes(m.id)}
										>
											{m.name}
										</option>
									{/each}
								</select>

								<button
									type="button"
									class="p-1 text-gray-400 hover:text-red-500"
									on:click={() => deleteChain(key)}
									aria-label={$i18n.t('Delete chain')}
									title={$i18n.t('Delete chain')}
								>
									<svg viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4">
										<path
											d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z"
										/>
									</svg>
								</button>
							</div>

							<!-- Ordered provider rows -->
							<div class="flex flex-col gap-1.5 mt-2">
								{#each chains[key] ?? [] as entry, idx (idx)}
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
												{#if entry.model_id && !providerModelsFor(key).find((m: any) => m.id === entry.model_id)}
													<option value={entry.model_id} class="text-gray-900">
														{entry.model_id} ({$i18n.t('missing')})
													</option>
												{/if}
												{#each providerModelsFor(key) as m (m.id)}
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
														setMaxConcurrent(key, idx, (e.currentTarget as HTMLInputElement).value)}
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
													on:click={() => moveProvider(key, idx, -1)}
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
													on:click={() => moveProvider(key, idx, 1)}
													disabled={idx === (chains[key] ?? []).length - 1}
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
													on:click={() => removeProvider(key, idx)}
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

							<button
								type="button"
								on:click={() => addProvider(key)}
								class="w-full text-sm border border-dashed border-gray-300 dark:border-gray-700 rounded-lg py-2 mt-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:border-gray-400 dark:hover:border-gray-600 transition"
							>
								+ {$i18n.t('Add provider')}
							</button>
						</div>
					{/each}
				</div>
			{/if}

			<button
				type="button"
				on:click={addChain}
				class="w-full text-sm font-medium border border-dashed border-gray-300 dark:border-gray-700 rounded-lg py-2.5 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 hover:border-gray-400 dark:hover:border-gray-600 transition"
			>
				+ {$i18n.t('Add chain')}
			</button>
		</AdminSettingSection>
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
