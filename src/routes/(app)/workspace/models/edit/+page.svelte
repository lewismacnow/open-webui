<script>
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';

	import { onMount, getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { page } from '$app/stores';
	import { config, models, settings } from '$lib/stores';

	import { getModelById, updateModelById } from '$lib/apis/models';

	import { getModels } from '$lib/apis';
	import ModelEditor from '$lib/components/workspace/Models/ModelEditor.svelte';

	let model = null;

	// A WRAPPER model is a custom model with a base model of its own; the
	// model registry marks those with `preset: true`. A base model (its id
	// belongs to a connection model) is NOT a wrapper and must not be given
	// a base model of its own. Deriving this from the registry stops a base
	// model from being edited as if it were a wrapper.
	$: isPresetModel = !!$models?.find((m) => m.id === model?.id)?.preset;

	onMount(async () => {
		const _id = $page.url.searchParams.get('id');
		if (_id) {
			model = await getModelById(localStorage.token, _id).catch((e) => {
				return null;
			});

			if (!model) {
				goto('/workspace/models');
				return;
			}

			if (!model?.write_access) {
				toast.error($i18n.t('You do not have permission to edit this model'));
				goto('/workspace/models');
			}
		} else {
			goto('/workspace/models');
		}
	});

	const onSubmit = async (modelInfo) => {
		const res = await updateModelById(localStorage.token, modelInfo.id, modelInfo);

		if (res) {
			try {
				await models.set(
					await getModels(
						localStorage.token,
						$config?.features?.enable_direct_connections
							? ($settings?.directConnections ?? null)
							: null
					)
				);
				toast.success($i18n.t('Model updated successfully'));
				await goto('/workspace/models');
			} catch (error) {
				toast.error(`${error}`);
			}
			return res;
		}
		return false;
	};
</script>

{#if model}
	<ModelEditor
		edit={true}
		{model}
		preset={isPresetModel}
		{onSubmit}
		onBack={async () => {
			await goto('/workspace/models');
		}}
	/>
{/if}
