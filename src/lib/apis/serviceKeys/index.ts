import { WEBUI_API_BASE_URL } from '$lib/constants';

// --- Service API Keys ---
//
// Admin-issued long-lived keys for inbound API traffic from a service
// (group-scoped). Backed by a parallel-lane backend that exposes the
// plaintext key exactly once on the create response — the UI is the only
// place we'll ever see it, so the "show it once" UX is load-bearing.

export type ServiceKey = {
	id: string;
	group_id: string;
	name: string;
	// Publicly visible prefix (e.g. "sk_live_a1b2"). Shown in full on
	// create, then only the last 4 chars are displayed in the table.
	prefix: string;
	// Unix seconds. Null = no expiry / never revoked.
	expires_at: number | null;
	ip_whitelist: string[];
	created_at: number;
	last_used_at: number | null;
	revoked_at: number | null;
	created_by: string | null;
};

export type CreateServiceKeyInput = {
	group_id: string;
	name: string;
	expires_at?: number | null;
	ip_whitelist?: string[];
};

// Create returns the canonical ServiceKey fields PLUS a one-shot
// `plaintext` value. The backend wipes it immediately after this
// response — we must surface it to the user before they navigate away.
export type CreateServiceKeyResponse = ServiceKey & { plaintext: string };

export type UpdateServiceKeyInput = {
	name?: string;
	expires_at?: number | null;
	ip_whitelist?: string[];
};

const authHeaders = (token: string) => ({
	'Content-Type': 'application/json',
	Authorization: `Bearer ${token}`
});

export const getServiceKeys = async (
	token: string,
	query?: string,
	group_id?: string,
	include_revoked?: boolean,
	signal?: AbortSignal
): Promise<{ items: ServiceKey[] }> => {
	let error = null;

	const searchParams = new URLSearchParams();
	if (query) searchParams.set('query', query);
	if (group_id) searchParams.set('group_id', group_id);
	if (include_revoked !== undefined) {
		searchParams.set('include_revoked', String(include_revoked));
	}

	const res = await fetch(`${WEBUI_API_BASE_URL}/service-keys/?${searchParams.toString()}`, {
		method: 'GET',
		signal,
		headers: authHeaders(token)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			if (signal?.aborted) return null;
			console.error('getServiceKeys:', err);
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return { items: res?.items ?? [] };
};

export const getServiceKey = async (token: string, id: string): Promise<ServiceKey | null> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/service-keys/${id}`, {
		method: 'GET',
		headers: authHeaders(token)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error('getServiceKey:', err);
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res ?? null;
};

export const createServiceKey = async (
	token: string,
	formData: CreateServiceKeyInput
): Promise<CreateServiceKeyResponse> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/service-keys/`, {
		method: 'POST',
		headers: authHeaders(token),
		body: JSON.stringify({
			group_id: formData.group_id,
			name: formData.name,
			expires_at: formData.expires_at ?? null,
			ip_whitelist: formData.ip_whitelist ?? []
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error('createServiceKey:', err);
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateServiceKey = async (
	token: string,
	id: string,
	patch: UpdateServiceKeyInput
): Promise<ServiceKey> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/service-keys/${id}`, {
		method: 'PATCH',
		headers: authHeaders(token),
		body: JSON.stringify(patch)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error('updateServiceKey:', err);
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const revokeServiceKey = async (
	token: string,
	id: string
): Promise<{ success: boolean }> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/service-keys/${id}/revoke`, {
		method: 'POST',
		headers: authHeaders(token)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error('revokeServiceKey:', err);
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res ?? { success: false };
};
