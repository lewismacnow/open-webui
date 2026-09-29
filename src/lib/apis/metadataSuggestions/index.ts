import { WEBUI_API_BASE_URL } from '$lib/constants';

// --- LLM Metadata Suggestions ---

export type MetadataScan = {
	id: string;
	knowledge_id: string;
	user_id: string;
	model_id: string;
	mode: 'all' | 'missing' | 'attributes' | 'file';
	attributes: string[] | null;
	file_id: string | null;
	max_parallel: number;
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
	proposed_title: string | null;
	proposed_description: string | null;
	proposed_summary: string | null;
	proposed_tags: string[] | null;
	previous_title: string | null;
	previous_description: string | null;
	previous_summary: string | null;
	previous_tags: string[] | null;
	redaction_count: number;
	proposer_model_id: string;
	status: 'pending' | 'applied' | 'dismissed';
	created_at: number;
};

export type StartScanInput = {
	knowledge_id: string;
	model_id: string;
	mode?: 'all' | 'missing' | 'attributes' | 'file';
	attributes?: string[] | null;
	file_id?: string | null;
	max_parallel?: number;
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
		status = 'pending',
		limit = 200
	}: { knowledgeId?: string | null; scanId?: string | null; status?: string; limit?: number }
): Promise<MetadataProposal[]> => {
	const params = new URLSearchParams({ status, limit: String(limit) });
	if (knowledgeId) params.set('knowledge_id', knowledgeId);
	if (scanId) params.set('scan_id', scanId);
	return req(`${base}/proposals?${params}`, token);
};

export const applyMetadataProposal = async (token: string, id: string): Promise<boolean> =>
	req(`${base}/proposals/${id}/apply`, token, { method: 'POST' });

export const dismissMetadataProposal = async (token: string, id: string): Promise<boolean> =>
	req(`${base}/proposals/${id}/dismiss`, token, { method: 'POST' });

export const applyAllMetadataProposals = async (
	token: string,
	knowledgeId: string,
	scanId?: string | null
): Promise<{ status: boolean; applied: number; skipped: string[] }> => {
	const params = new URLSearchParams({ knowledge_id: knowledgeId });
	if (scanId) params.set('scan_id', scanId);
	return req(`${base}/proposals/apply-all?${params}`, token, { method: 'POST' });
};
