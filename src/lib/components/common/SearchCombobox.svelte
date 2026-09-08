<script lang="ts">
	/**
	 * SearchCombobox
	 * --------------
	 * A debounced, keyboard-navigable combobox for picking an id from a
	 * server-side search endpoint. Caller owns the selected `value` (id);
	 * the internal `selectedLabel` tracks the most recently picked label
	 * so we can display something nicer than the raw id when the input is
	 * empty and the dropdown is closed.
	 *
	 * Usage:
	 *   <SearchCombobox
	 *     bind:value={groupId}
	 *     placeholder="Search groups"
	 *     endpoint={async (q) => (await searchGroups(...)).items.map(g => ({id: g.id, label: g.name}))}
	 *     onChange={(id, label) => groupLabel = label}
	 *   />
	 */
	import { onMount, tick } from 'svelte';
	import { getContext } from 'svelte';

	export let value: string = '';
	export let placeholder: string = '';
	export let disabled: boolean = false;
	// Caller-supplied fetcher. Returns {id, label}[]. The component never
	// calls a URL directly so the same component works for users, groups,
	// models, etc. — caller does the mapping.
	export let endpoint: (query: string) => Promise<{ id: string; label: string }[]>;
	// Fires when the user picks an item from the dropdown (mouse or
	// keyboard). Caller should mirror the new label back into whatever
	// application state needs it.
	export let onChange: (newId: string, newLabel: string) => void = () => {};

	const i18n: any = getContext('i18n');

	let query: string = '';
	let selectedLabel: string = '';
	let options: { id: string; label: string }[] = [];
	let open: boolean = false;
	let highlight: number = -1;

	// 200ms debounce per spec.
	let fetchTimer: ReturnType<typeof setTimeout> | null = null;
	let fetchSeq: number = 0;
	let fetching: boolean = false;

	// Wrap the click-outside handler so we can detach it cleanly when the
	// component unmounts. Without this the listener would keep firing on
	// a detached DOM node and we'd close the dropdown when it shouldn't.
	let rootEl: HTMLElement | null = null;

	function scheduleFetch(text: string) {
		if (fetchTimer) clearTimeout(fetchTimer);
		fetchTimer = setTimeout(() => {
			fetchTimer = null;
			fetchNow(text);
		}, 200);
	}

	async function fetchNow(text: string) {
		const mySeq = ++fetchSeq;
		fetching = true;
		try {
			const out = await endpoint(text);
			if (mySeq !== fetchSeq) return;
			options = Array.isArray(out) ? out : [];
			// If we have an existing value and no query, keep the cached
			// label if it still matches an option; otherwise try to
			// resolve it from the just-fetched list.
			if (!text && value && !selectedLabel) {
				const match = options.find((o) => o.id === value);
				if (match) selectedLabel = match.label;
			}
			// Clamp highlight if the option list shrank.
			if (highlight >= options.length) highlight = options.length - 1;
			if (highlight < -1) highlight = -1;
		} catch (e) {
			if (mySeq !== fetchSeq) return;
			console.error('SearchCombobox endpoint error:', e);
			options = [];
		} finally {
			if (mySeq === fetchSeq) fetching = false;
		}
	}

	async function pick(opt: { id: string; label: string }) {
		value = opt.id;
		selectedLabel = opt.label;
		onChange(opt.id, opt.label);
		query = '';
		options = [];
		highlight = -1;
		open = false;
		await tick();
	}

	function onInput(e: Event) {
		query = (e.currentTarget as HTMLInputElement).value;
		open = true;
		highlight = options.length > 0 ? 0 : -1;
		scheduleFetch(query);
	}

	function onFocus() {
		open = true;
		// Always re-fetch on focus so the dropdown can show fresh data
		// even when the user hasn't typed yet.
		scheduleFetch(query);
	}

	function onKeyDown(e: KeyboardEvent) {
		if (!open) {
			if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter') {
				open = true;
				highlight = options.length > 0 ? 0 : -1;
				scheduleFetch(query);
				e.preventDefault();
				return;
			}
		}

		if (e.key === 'ArrowDown') {
			if (options.length === 0) return;
			highlight = (highlight + 1) % options.length;
			e.preventDefault();
		} else if (e.key === 'ArrowUp') {
			if (options.length === 0) return;
			highlight = highlight <= 0 ? options.length - 1 : highlight - 1;
			e.preventDefault();
		} else if (e.key === 'Enter') {
			if (highlight >= 0 && options[highlight]) {
				pick(options[highlight]);
				e.preventDefault();
			}
		} else if (e.key === 'Escape') {
			open = false;
			highlight = -1;
			e.preventDefault();
		}
	}

	function onBlur() {
		// Defer close so click-on-option still registers.
		setTimeout(() => {
			open = false;
			highlight = -1;
		}, 150);
	}

	function onDocClick(e: MouseEvent) {
		if (!rootEl) return;
		if (e.target instanceof Node && rootEl.contains(e.target)) return;
		open = false;
		highlight = -1;
	}

	// External `value` changed (parent reassigned): if we don't have a
	// label cached for it, drop the cache so the next focus shows the
	// dropdown from a clean slate. The dropdown will offer the option
	// matching this id once it loads, and `onChange` will re-sync the
	// label.
	$: if (value !== undefined && (selectedLabel === '' || selectedLabel == null)) {
		// Try to resolve from current option list first.
		const match = options.find((o) => o.id === value);
		if (match) selectedLabel = match.label;
	}

	onMount(() => {
		// Initial fetch with empty query — caller decides whether this
		// returns "first N", "all", or "top of alphabet." Lets us render
		// a useful dropdown on focus without typing.
		fetchNow('');
		document.addEventListener('mousedown', onDocClick, true);
		return () => {
			document.removeEventListener('mousedown', onDocClick, true);
			if (fetchTimer) clearTimeout(fetchTimer);
		};
	});
</script>

<div class="relative w-full" bind:this={rootEl}>
	<input
		type="text"
		class="flex-1 min-w-40 text-xs bg-transparent outline-none border-b border-gray-200 dark:border-gray-700 focus:border-gray-400 dark:focus:border-gray-500 py-0.5 disabled:opacity-50 disabled:cursor-not-allowed"
		{placeholder}
		{disabled}
		value={query || selectedLabel}
		autocomplete="off"
		role="combobox"
		aria-expanded={open}
		aria-autocomplete="list"
		on:input={onInput}
		on:focus={onFocus}
		on:blur={onBlur}
		on:keydown={onKeyDown}
	/>

	{#if open}
		<div
			class="absolute z-50 left-0 right-0 mt-1 max-h-56 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg"
			role="listbox"
		>
			{#if fetching && options.length === 0}
				<div class="px-2 py-1.5 text-xs text-gray-400 dark:text-gray-500">
					{$i18n.t('Loading...')}
				</div>
			{:else if options.length === 0}
				<div class="px-2 py-1.5 text-xs text-gray-400 dark:text-gray-500">
					{$i18n.t('No matches')}
				</div>
			{:else}
				{#each options as opt, i (opt.id)}
					<button
						type="button"
						role="option"
						aria-selected={highlight === i}
						class="w-full text-left px-2 py-1 text-xs flex items-center justify-between gap-2 transition-colors {highlight ===
						i
							? 'bg-gray-100 dark:bg-white/10 text-gray-900 dark:text-white'
							: 'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-white/5'}"
						on:mousedown|preventDefault={() => pick(opt)}
						on:mouseenter={() => (highlight = i)}
					>
						<span class="truncate">{opt.label}</span>
						<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600 truncate font-mono">
							{opt.id}
						</span>
					</button>
				{/each}
			{/if}
		</div>
	{/if}
</div>
