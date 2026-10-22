import { WEBUI_API_BASE_URL } from '$lib/constants';

// --- LLM Metadata Enrichment ---

export type KnowledgeEnrichmentFile = {
	id: string;
	filename: string | null;
	title: string | null;
	ignored: boolean;
	ignored_reason: string | null;
	ignored_at: number | null;
	improved_at: number | null;
	size: number | null;
	content_type: string | null;
};

export type MetadataScan = {
	id: string;
	knowledge_id: string;
	user_id: string;
	model_id: string;
	mode: 'all' | 'missing' | 'attributes' | 'file';
	attributes: string[] | null;
	file_id: string | null;
	max_parallel: number;
	test_run: boolean;
	test_preview: Record<string, any> | null;
	reembedded: number;
	content_updated: number;
	titles_updated: number;
	ignored: number;
	status: 'running' | 'completed' | 'failed' | 'cancelled';
	total_files: number;
	processed_files: number;
	proposals_created: number;
	redactions: number;
	errors: string[] | null;
	started_at: number;
	finished_at: number | null;
};

export type MetadataProposal = {
	id: string;
	scan_id: string;
	knowledge_id: string;
	file_id: string;
	user_id: string;
	proposed_title: string | null;
	proposed_description: string | null;
	proposed_summary: string | null;
	proposed_tags: string[] | null;
	proposed_keywords: string[] | null;
	proposed_extra: Record<string, string> | null;
	proposed_content: string | null;
	previous_title: string | null;
	previous_description: string | null;
	previous_summary: string | null;
	previous_tags: string[] | null;
	previous_keywords: string[] | null;
	previous_extra: Record<string, string> | null;
	previous_content_excerpt: string | null;
	content_changed: boolean;
	redaction_count: number;
	proposer_model_id: string;
	status: 'pending' | 'applied' | 'dismissed';
	created_at: number;
	applied_at: number | null;
	applied_by: string | null;
};

export type StartScanInput = {
	knowledge_id: string;
	model_id: string;
	mode?: 'all' | 'missing' | 'attributes' | 'file';
	attributes?: string[] | null;
	file_id?: string | null;
	max_parallel?: number;
	test_run?: boolean;
	improve_content?: boolean;
	update_titles?: boolean;
	ignore_low_value?: boolean;
	force?: boolean;
	include_ignored?: boolean;
};

const base = `${WEBUI_API_BASE_URL}/metadata-suggestions`;

async function req(url: string, token: string, options: RequestInit = {}) {
	const res = await fetch(url, {
		...options,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`,
			...(options.headers || {})
		}
	});
	if (!res.ok) {
		let detail = res.statusText;
		try {
			const body = await res.json();
			if (body?.detail)
				detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
		} catch (_) {}
		throw new Error(detail);
	}
	return res.json();
}

export const startMetadataScan = async (
	token: string,
	input: StartScanInput
): Promise<MetadataScan> =>
	req(`${base}/scan`, token, { method: 'POST', body: JSON.stringify(input) });

export const getMetadataScans = async (
	token: string,
	knowledgeId?: string | null,
	limit = 20
): Promise<MetadataScan[]> =>
	req(`${base}/scans?limit=${limit}${knowledgeId ? `&knowledge_id=${knowledgeId}` : ''}`, token);

export const cancelMetadataScan = async (token: string, id: string): Promise<boolean> =>
	req(`${base}/scans/${id}/cancel`, token, { method: 'POST' });

export const getMetadataScan = async (token: string, id: string): Promise<MetadataScan> =>
	req(`${base}/scans/${id}`, token);

export const getMetadataProposals = async (
	token: string,
	{
		knowledgeId,
		scanId,
		status,
		limit = 200
	}: {
		knowledgeId?: string | null;
		scanId?: string | null;
		status?: string | null;
		limit?: number;
	}
): Promise<MetadataProposal[]> => {
	const params = new URLSearchParams({ limit: String(limit) });
	if (status) params.set('status', status);
	if (knowledgeId) params.set('knowledge_id', knowledgeId);
	if (scanId) params.set('scan_id', scanId);
	return req(`${base}/proposals?${params}`, token);
};

export const getEnrichmentFiles = async (
	token: string,
	knowledgeId: string,
	includeIgnored = false
): Promise<KnowledgeEnrichmentFile[]> => {
	const params = new URLSearchParams({ knowledge_id: knowledgeId });
	if (includeIgnored) params.set('include_ignored', 'true');
	return req(`${base}/files?${params}`, token);
};

export const ignoreEnrichmentFile = async (
	token: string,
	fileId: string,
	reason?: string
): Promise<{ status: boolean }> =>
	req(`${base}/files/${fileId}/ignore`, token, {
		method: 'POST',
		body: JSON.stringify({ reason: reason ?? null })
	});

export const unignoreEnrichmentFile = async (
	token: string,
	fileId: string
): Promise<{ status: boolean }> =>
	req(`${base}/files/${fileId}/unignore`, token, { method: 'POST' });
