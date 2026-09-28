import { WEBUI_API_BASE_URL } from '$lib/constants';

// --- GitHub Knowledge Sync ---
//
// Credentials are write-only from the UI's perspective: the token is sent
// once on create and never read back (only a 4-char suffix is stored).

export type GithubCredential = {
	id: string;
	name: string;
	token_suffix: string;
	note: string | null;
	created_by: string;
	last_used_at: number | null;
	created_at: number;
	updated_at: number;
	source_count: number;
};

export type GithubSyncSource = {
	id: string;
	knowledge_id: string;
	knowledge_name?: string | null;
	repo_full_name?: string;
	user_id: string;
	repo_owner: string;
	repo_name: string;
	branch: string;
	directory_path: string;
	credential_id: string | null;
	include_globs: string[] | null;
	exclude_globs: string[] | null;
	max_file_bytes: number;
	max_files: number;
	remove_deleted: boolean;
	interval_seconds: number | null;
	next_run_at: number | null;
	enabled: boolean;
	last_commit_sha: string | null;
	last_sync_at: number | null;
	last_sync_status: string | null;
	last_sync_result: Record<string, any> | null;
	consecutive_failures: number;
	created_at: number;
	updated_at: number;
};

export type CreateGithubSyncSourceInput = {
	knowledge_id: string;
	repo_owner: string;
	repo_name: string;
	branch?: string;
	directory_path?: string;
	credential_id?: string | null;
	include_globs?: string[] | null;
	exclude_globs?: string[] | null;
	max_file_bytes?: number;
	max_files?: number;
	remove_deleted?: boolean;
	interval_seconds?: number | null;
	enabled?: boolean;
};

const credsUrl = `${WEBUI_API_BASE_URL}/github-sync/credentials`;
const sourcesUrl = `${WEBUI_API_BASE_URL}/github-sync/sources`;

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

// Credentials

export const createGithubCredential = async (
	token: string,
	input: { name: string; token: string; note?: string | null }
): Promise<GithubCredential> =>
	req(credsUrl, token, { method: 'POST', body: JSON.stringify(input) });

export const getGithubCredentials = async (token: string): Promise<GithubCredential[]> =>
	req(credsUrl, token);

export const deleteGithubCredential = async (token: string, id: string): Promise<boolean> =>
	req(`${credsUrl}/${id}`, token, { method: 'DELETE' });

// Sources

export const getGithubSyncSources = async (token: string): Promise<GithubSyncSource[]> =>
	req(sourcesUrl, token);

export const createGithubSyncSource = async (
	token: string,
	input: CreateGithubSyncSourceInput
): Promise<GithubSyncSource> =>
	req(sourcesUrl, token, { method: 'POST', body: JSON.stringify(input) });

export const updateGithubSyncSource = async (
	token: string,
	id: string,
	input: Partial<CreateGithubSyncSourceInput>
): Promise<GithubSyncSource> =>
	req(`${sourcesUrl}/${id}`, token, { method: 'PATCH', body: JSON.stringify(input) });

export const deleteGithubSyncSource = async (
	token: string,
	id: string,
	removeFiles = false
): Promise<{ status: boolean; files_removed: number }> =>
	req(`${sourcesUrl}/${id}?remove_files=${removeFiles}`, token, { method: 'DELETE' });

export const syncGithubSyncSourceNow = async (
	token: string,
	id: string
): Promise<Record<string, any>> => req(`${sourcesUrl}/${id}/sync`, token, { method: 'POST' });
