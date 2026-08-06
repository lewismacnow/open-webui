<script lang="ts">
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { models } from '$lib/stores';
	import { generateTool } from '$lib/apis/tools';
	import { toast } from 'svelte-sonner';

	import Modal from '$lib/components/common/Modal.svelte';
	import CodeEditor from '$lib/components/common/CodeEditor.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	export let show = false;
	export let existingCode = '';
	export let existingName = '';
	export let existingDescription = '';
	export let onAccept = (_result: { code: string; name: string; description: string }) => {};

	let selectedModel = '';
	let prompt = '';
	let generating = false;
	let result: {
		code: string;
		name: string;
		description: string;
		validation_passed: boolean;
		validation_message: string;
		attempts: number;
	} | null = null;
	let validationStatus = '';

	let filteredModels: typeof $models = [];

	$: filteredModels = $models.filter((m) => !(m?.info?.meta?.hidden ?? false));

	// Initialize selected model with default when modal opens
	$: if (show && !selectedModel && filteredModels.length > 0) {
		selectedModel = filteredModels[0]?.id ?? '';
	}

	// Pre-fill prompt when editing
	$: if (show && existingCode && !prompt) {
		prompt = $i18n.t('Improve or modify this tool...');
	}

	async function handleGenerate() {
		if (!selectedModel) {
			toast.error($i18n.t('Please select a model'));
			return;
		}
		if (!prompt.trim()) {
			toast.error($i18n.t('Please describe what you want the tool to do'));
			return;
		}

		generating = true;
		result = null;
		validationStatus = '';

		try {
			const response = await generateTool(localStorage.token, {
				model: selectedModel,
				prompt: prompt,
				existing_code: existingCode || undefined,
				existing_name: existingName || undefined,
				existing_description: existingDescription || undefined
			});

			result = response;

			if (response.validation_passed) {
				validationStatus = 'success';
				toast.success(
					$i18n.t('Tool generated successfully') +
						` (${response.attempts} attempt${response.attempts > 1 ? 's' : ''})`
				);
			} else {
				validationStatus = 'warning';
				toast.warning(
					$i18n.t('Tool generated with validation issues') +
						`: ${response.validation_message}`
				);
			}
		} catch (e) {
			toast.error($i18n.t('Failed to generate tool'));
			console.error(e);
		} finally {
			generating = false;
		}
	}

	function handleAccept() {
		if (result && result.code) {
			onAccept({
				code: result.code,
				name: result.name,
				description: result.description
			});
			show = false;
			prompt = '';
			result = null;
		}
	}

	function handleRegenerate() {
		result = null;
		validationStatus = '';
		handleGenerate();
	}
</script>

<Modal size="lg" bind:show>
	<div>
		<!-- Header -->
		<div class="flex justify-between dark:text-gray-100 px-4 pt-3 pb-1">
			<div class="text-base font-normal self-center">
				{$i18n.t(existingCode ? 'Edit Tool with AI' : 'Generate Tool with AI')}
			</div>
			<button
				class="self-center rounded-lg p-1 text-gray-500 transition hover:bg-gray-50 hover:text-gray-700 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
				on:click={() => {
					show = false;
				}}
			>
				<XMark className="size-4" />
			</button>
		</div>

		<!-- Body -->
		<div class="max-h-[80vh] overflow-y-auto px-4 pb-4">
			<!-- Model Selector -->
			<div class="mt-3">
				<label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
					{$i18n.t('Model')}
				</label>
				<select
					bind:value={selectedModel}
					class="w-full rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent px-3 py-2 text-sm text-gray-900 dark:text-gray-100 outline-hidden focus:border-gray-400 dark:focus:border-gray-500"
				>
					{#each filteredModels as model (model.id)}
						<option value={model.id}>{model.name}</option>
					{/each}
				</select>
			</div>

			<!-- Prompt Textarea -->
			<div class="mt-3">
				<label class="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
					{$i18n.t('Prompt')}
				</label>
				<textarea
					bind:value={prompt}
					class="w-full resize-none rounded-lg border border-gray-300 dark:border-gray-700 bg-transparent px-3 py-2 text-sm text-gray-900 dark:text-gray-100 outline-hidden focus:border-gray-400 dark:focus:border-gray-500"
					rows="4"
					placeholder={$i18n.t(
						existingCode
							? 'Describe the changes you want to make to this tool...'
							: 'Describe the tool you want to create, e.g. "A calculator tool that can perform basic arithmetic operations and return the result"'
					)}
				></textarea>
			</div>

			<!-- Generate Button -->
			<button
				class="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-purple-500 to-indigo-500 px-4 py-2.5 text-sm font-medium text-white transition hover:from-purple-600 hover:to-indigo-600 disabled:opacity-60"
				on:click={handleGenerate}
				disabled={generating}
				type="button"
			>
				{#if generating}
					<Spinner className="size-4" />
					{$i18n.t('Generating...')}
				{:else}
					<svg
						xmlns="http://www.w3.org/2000/svg"
						fill="none"
						viewBox="0 0 24 24"
						stroke-width="2"
						stroke="currentColor"
						class="size-4"
					>
						<path
							stroke-linecap="round"
							stroke-linejoin="round"
							d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.456-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 0 0-2.455 2.456ZM16.894 20.567 16.5 21.75l-.394-1.183a2.25 2.25 0 0 0-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 0 0 1.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 0 0 1.423 1.423l1.183.394-1.183.394a2.25 2.25 0 0 0-1.423 1.423Z"
						/>
					</svg>
					{$i18n.t('Generate')}
				{/if}
			</button>

			<!-- Result Section -->
			{#if result}
				<div class="mt-4 border-t border-gray-200 dark:border-gray-700 pt-4">
					<!-- Validation Badge -->
					<div class="mb-2 flex items-center gap-2">
						<span
							class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium {validationStatus === 'success'
								? 'bg-green-500/20 text-green-700 dark:text-green-300'
								: 'bg-amber-500/20 text-amber-700 dark:text-amber-300'}"
						>
							{#if validationStatus === 'success'}
								<svg
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
									stroke-width="2"
									stroke="currentColor"
									class="size-3.5"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
									/>
								</svg>
							{:else}
								<svg
									xmlns="http://www.w3.org/2000/svg"
									fill="none"
									viewBox="0 0 24 24"
									stroke-width="2"
									stroke="currentColor"
									class="size-3.5"
								>
									<path
										stroke-linecap="round"
										stroke-linejoin="round"
										d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"
									/>
								</svg>
							{/if}
							{validationStatus === 'success'
								? $i18n.t('Validation passed')
								: $i18n.t('Validation warning')}
						</span>
						{#if result.validation_message}
							<span class="text-xs text-gray-500 dark:text-gray-400">
								{result.validation_message}
							</span>
						{/if}
					</div>

					<!-- Code Preview -->
					<div class="mb-3 min-h-[200px] overflow-hidden rounded-lg border border-gray-300 dark:border-gray-700">
						<CodeEditor
							value={result.code}
							lang="python"
							className="text-[11px]"
							onChange={() => {}}
							onSave={() => {}}
						/>
					</div>

					<!-- Action Buttons -->
					<div class="flex gap-2">
						<button
							class="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-green-700"
							on:click={handleAccept}
							type="button"
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="2"
								stroke="currentColor"
								class="size-4"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="m4.5 12.75 6 6 9-13.5"
								/>
							</svg>
							{$i18n.t('Accept')}
						</button>
						<button
							class="flex items-center justify-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 transition hover:bg-gray-50 dark:hover:bg-gray-800"
							on:click={handleRegenerate}
							type="button"
							disabled={generating}
						>
							<svg
								xmlns="http://www.w3.org/2000/svg"
								fill="none"
								viewBox="0 0 24 24"
								stroke-width="2"
								stroke="currentColor"
								class="size-4"
							>
								<path
									stroke-linecap="round"
									stroke-linejoin="round"
									d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182"
								/>
							</svg>
							{$i18n.t('Regenerate')}
						</button>
					</div>
				</div>
			{/if}
		</div>
	</div>
</Modal>
