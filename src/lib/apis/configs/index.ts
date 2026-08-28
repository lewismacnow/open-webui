import { WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';
import type { Banner } from '$lib/types';

export const importConfig = async (token: string, config: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/import`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			config: config
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const exportConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/export`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getConnectionsConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/connections`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const setConnectionsConfig = async (token: string, config: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/connections`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...config
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getToolServerConnections = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/tool_servers`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const setToolServerConnections = async (token: string, connections: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/tool_servers`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...connections
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getTerminalServerConnections = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const setTerminalServerConnections = async (token: string, connections: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...connections
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

/**
 * Detect whether a terminal server URL points to an Orchestrator or a direct
 * Open Terminal instance.
 *
 * - GET {url}/api/v1/policies → 200 → "orchestrator"
 * - GET {url}/api/config      → 200 → "terminal"
 * - Neither                         → null
 */
export const detectTerminalServerType = async (
	url: string,
	key: string
): Promise<'orchestrator' | 'terminal' | null> => {
	const baseUrl = url.replace(/\/$/, '');
	const headers: Record<string, string> = {};
	if (key) {
		headers['Authorization'] = `Bearer ${key}`;
	}

	// Orchestrators expose a policies API; plain terminals don't.
	try {
		const res = await fetch(`${baseUrl}/api/v1/policies`, { headers });
		if (res.ok) return 'orchestrator';
	} catch {
		// ignore
	}

	// Fall back to open-terminal config endpoint.
	try {
		const res = await fetch(`${baseUrl}/api/config`, { headers });
		if (res.ok) return 'terminal';
	} catch {
		// ignore
	}

	return null;
};

/**
 * Create or update a policy on the orchestrator.
 * Proxied through the Open WebUI backend to keep API keys server-side.
 */
export const putOrchestratorPolicy = async (
	token: string,
	url: string,
	key: string,
	policyId: string,
	policyData: object,
	authType: string = 'bearer'
): Promise<object | null> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers/policy`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			url: url.replace(/\/$/, ''),
			key,
			auth_type: authType,
			policy_id: policyId,
			policy_data: policyData
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getOrchestratorPolicy = async (
	token: string,
	url: string,
	key: string,
	policyId: string,
	authType: string = 'bearer'
): Promise<any> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers/policy`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			url: url.replace(/\/$/, ''),
			key,
			auth_type: authType,
			policy_id: policyId
		})
	});
	if (!res.ok) {
		const body = await res.json();
		throw Object.assign(new Error(body.detail || 'Failed to read policy'), { status: res.status });
	}
	return res.json();
};

export const putOrchestratorLifecycle = async (
	token: string,
	url: string,
	key: string,
	policyId: string,
	lifecycleData: object,
	authType: string = 'bearer'
): Promise<object | null> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers/lifecycle`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			url: url.replace(/\/$/, ''),
			key,
			auth_type: authType,
			policy_id: policyId,
			lifecycle_data: lifecycleData
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getOrchestratorLifecycle = async (
	token: string,
	url: string,
	key: string,
	policyId: string,
	authType: string = 'bearer'
): Promise<any> => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers/lifecycle`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			url: url.replace(/\/$/, ''),
			key,
			auth_type: authType,
			policy_id: policyId
		})
	});
	if (!res.ok) {
		const body = await res.json();
		throw Object.assign(new Error(body.detail || 'Failed to read lifecycle'), {
			status: res.status
		});
	}
	return res.json();
};

export const refreshOrchestratorTerminals = async (
	token: string,
	url: string,
	key: string,
	body: {
		user_id?: string;
		policy_id?: string;
		only_idle?: boolean;
		reset?: boolean;
	},
	authType: string = 'bearer'
): Promise<object | null> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers/refresh`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			url: url.replace(/\/$/, ''),
			key,
			auth_type: authType,
			...body
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

/**
 * Verify a terminal server connection via the backend proxy.
 * Used for system/admin connections to avoid CORS issues and API key exposure.
 */
export const verifyTerminalServerConnection = async (token: string, connection: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/terminal_servers/verify`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...connection
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const verifyToolServerConnection = async (token: string, connection: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/tool_servers/verify`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...connection
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

type RegisterOAuthClientForm = {
	url: string;
	client_id: string;
	client_name?: string;
	client_secret?: string;
	oauth_server_url?: string;
	oauth_scope?: string;
};

export const registerOAuthClient = async (
	token: string,
	formData: RegisterOAuthClientForm,
	type: null | string = null
) => {
	let error = null;

	const searchParams = type ? `?type=${type}` : '';
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/oauth/clients/register${searchParams}`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...formData
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getOAuthClientAuthorizationUrl = (clientId: string, type: null | string = null) => {
	const oauthClientId = type ? `${type}:${clientId}` : clientId;
	return `${WEBUI_BASE_URL}/oauth/clients/${oauthClientId}/authorize`;
};

export const initiateOAuthRedirect = (tool: {
	id: string;
	serverId: string;
	authType?: string | null;
}) => {
	sessionStorage.setItem('pendingOAuthToolId', tool.id);
	sessionStorage.setItem('oauthRedirectInProgressToolId', tool.id);
	const authUrl = getOAuthClientAuthorizationUrl(tool.serverId, tool.authType ?? 'mcp');
	window.open(authUrl, '_self', 'noopener');
};

export const getCodeExecutionConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/code_execution`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const setCodeExecutionConfig = async (token: string, config: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/code_execution`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...config
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getModelsDefaults = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/models/defaults`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getModelsConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/models`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const setModelsConfig = async (token: string, config: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/models`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...config
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// --- Model Failover ---

export type ModelFailoverEntry = {
	model_id: string;
	// Optional for backward compatibility: older saved entries may still
	// carry per-provider capability tags. No longer rendered or written.
	capabilities?: string[];
};

export type ModelFailoverMap = Record<string, ModelFailoverEntry[]>;

/**
 * Fetch the global per-base-model failover map.
 *
 * Admin-only on the backend; returns an empty object when no failover has
 * been configured for any model.
 */
export const getModelFailoverMap = async (token: string): Promise<ModelFailoverMap> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/models/failover`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res?.MODEL_FAILOVER_MAP ?? {};
};

export const setModelFailoverMap = async (
	token: string,
	map: ModelFailoverMap
): Promise<ModelFailoverMap> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/models/failover`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({ MODEL_FAILOVER_MAP: map })
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res?.MODEL_FAILOVER_MAP ?? {};
};

// --- Wrapper Model Provider Chains ---

export type WrapperChainEntry = {
	model_id: string;
	// null = unlimited
	max_concurrent: number | null;
};

// The chain is global — one list applied to every wrapper model with
// failover_source='global'. Not keyed by wrapper id.
export type WrapperProviderChains = WrapperChainEntry[];

/**
 * Fetch the global wrapper provider chain.
 *
 * Admin-only on the backend; returns an empty array when no chain has
 * been configured.
 */
export const getWrapperProviderChains = async (token: string): Promise<WrapperProviderChains> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/models/wrapper-chains`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res?.WRAPPER_PROVIDER_CHAINS ?? [];
};

export const setWrapperProviderChains = async (
	token: string,
	chain: WrapperProviderChains
): Promise<WrapperProviderChains> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/models/wrapper-chains`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({ WRAPPER_PROVIDER_CHAINS: chain })
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res?.WRAPPER_PROVIDER_CHAINS ?? [];
};

// --- Tools Config ---

export type ToolsConfig = {
	match_budget_seconds: number;
	max_regex_quantifier_count: number;
	max_regex_quantifier_expansion: number;
	kb_exec_max_output_chars: number;
	kb_exec_max_grep_files: number;
	knowledge_grep_max_matches: number;
	view_file_max_chars: number;
	view_file_default_max_chars: number;
};

export const getToolsConfig = async (token: string): Promise<ToolsConfig> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/tools`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return {
		match_budget_seconds: res?.match_budget_seconds ?? 5,
		max_regex_quantifier_count: res?.max_regex_quantifier_count ?? 2000,
		max_regex_quantifier_expansion: res?.max_regex_quantifier_expansion ?? 100000,
		kb_exec_max_output_chars: res?.kb_exec_max_output_chars ?? 30000,
		kb_exec_max_grep_files: res?.kb_exec_max_grep_files ?? 200,
		knowledge_grep_max_matches: res?.knowledge_grep_max_matches ?? 50,
		view_file_max_chars: res?.view_file_max_chars ?? 100000,
		view_file_default_max_chars: res?.view_file_default_max_chars ?? 10000
	};
};

export const setToolsConfig = async (token: string, config: ToolsConfig): Promise<ToolsConfig> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/tools`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			match_budget_seconds: config.match_budget_seconds,
			max_regex_quantifier_count: config.max_regex_quantifier_count,
			max_regex_quantifier_expansion: config.max_regex_quantifier_expansion,
			kb_exec_max_output_chars: config.kb_exec_max_output_chars,
			kb_exec_max_grep_files: config.kb_exec_max_grep_files,
			knowledge_grep_max_matches: config.knowledge_grep_max_matches,
			view_file_max_chars: config.view_file_max_chars,
			view_file_default_max_chars: config.view_file_default_max_chars
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return {
		match_budget_seconds: res?.match_budget_seconds ?? config.match_budget_seconds,
		max_regex_quantifier_count:
			res?.max_regex_quantifier_count ?? config.max_regex_quantifier_count,
		max_regex_quantifier_expansion:
			res?.max_regex_quantifier_expansion ?? config.max_regex_quantifier_expansion,
		kb_exec_max_output_chars: res?.kb_exec_max_output_chars ?? config.kb_exec_max_output_chars,
		kb_exec_max_grep_files: res?.kb_exec_max_grep_files ?? config.kb_exec_max_grep_files,
		knowledge_grep_max_matches:
			res?.knowledge_grep_max_matches ?? config.knowledge_grep_max_matches,
		view_file_max_chars: res?.view_file_max_chars ?? config.view_file_max_chars,
		view_file_default_max_chars:
			res?.view_file_default_max_chars ?? config.view_file_default_max_chars
	};
};

// --- Token Caps ---

export type TokenCap = {
	target_type: 'user' | 'group' | 'model' | 'api_key';
	target_id: string;
	// Admin-facing values are in millions of tokens (1 = 1M tokens).
	// 0 means unlimited. The backend multiplies by 1_000_000 to store
	// raw tokens.
	hourly_millions: number;
	daily_millions: number;
	weekly_millions: number;
	monthly_millions: number;
};

export type TokenCapsConfig = { caps: TokenCap[] };

export const getTokenCaps = async (token: string): Promise<TokenCapsConfig> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/token-caps`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return { caps: res?.caps ?? [] };
};

export const setTokenCaps = async (
	token: string,
	config: TokenCapsConfig
): Promise<TokenCapsConfig> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/token-caps`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({ caps: config.caps })
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return { caps: res?.caps ?? config.caps };
};

// --- API Token Usage Analytics ---
//
// These aggregate the `api_token_usage` table populated on the
// OpenAI-compatible API path (invisible to the chat-based analytics
// dashboard, which only reads `chat_message.usage`).

export type ApiKeyTokenUsageEntry = {
	api_key_id: string;
	prompt_tokens: number;
	completion_tokens: number;
	total_tokens: number;
	request_count: number;
};

export type ApiKeyTokenUsageResponse = {
	keys: ApiKeyTokenUsageEntry[];
	total_prompt_tokens: number;
	total_completion_tokens: number;
	total_tokens: number;
	total_request_count: number;
};

export const getApiKeyTokenUsage = async (
	token: string,
	limit?: number,
	startDate?: number,
	endDate?: number
): Promise<ApiKeyTokenUsageResponse> => {
	let error = null;

	const searchParams = new URLSearchParams();
	if (limit != null) searchParams.set('limit', `${limit}`);
	if (startDate != null) searchParams.set('start_date', `${startDate}`);
	if (endDate != null) searchParams.set('end_date', `${endDate}`);

	const res = await fetch(
		`${WEBUI_API_BASE_URL}/analytics/api-keys/tokens?${searchParams.toString()}`,
		{
			method: 'GET',
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${token}`
			}
		}
	)
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return {
		keys: res?.keys ?? [],
		total_prompt_tokens: res?.total_prompt_tokens ?? 0,
		total_completion_tokens: res?.total_completion_tokens ?? 0,
		total_tokens: res?.total_tokens ?? 0,
		total_request_count: res?.total_request_count ?? 0
	};
};

export type EndpointTokenUsageEntry = {
	endpoint: string;
	prompt_tokens: number;
	completion_tokens: number;
	total_tokens: number;
	request_count: number;
};

export type EndpointTokenUsageResponse = {
	endpoints: EndpointTokenUsageEntry[];
	total_prompt_tokens: number;
	total_completion_tokens: number;
	total_tokens: number;
	total_request_count: number;
};

export const getEndpointTokenUsage = async (
	token: string,
	startDate?: number,
	endDate?: number
): Promise<EndpointTokenUsageResponse> => {
	let error = null;

	const searchParams = new URLSearchParams();
	if (startDate != null) searchParams.set('start_date', `${startDate}`);
	if (endDate != null) searchParams.set('end_date', `${endDate}`);

	const res = await fetch(
		`${WEBUI_API_BASE_URL}/analytics/endpoints/tokens?${searchParams.toString()}`,
		{
			method: 'GET',
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${token}`
			}
		}
	)
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return {
		endpoints: res?.endpoints ?? [],
		total_prompt_tokens: res?.total_prompt_tokens ?? 0,
		total_completion_tokens: res?.total_completion_tokens ?? 0,
		total_tokens: res?.total_tokens ?? 0,
		total_request_count: res?.total_request_count ?? 0
	};
};

// --- Vision Image RAG ---

/**
 * Fetch the global vision-support model id used by Vision Image RAG.
 *
 * Empty string means "not set" (only vision-capable chatting models get
 * image RAG). Admin-only on the backend.
 */
export const getRagVisionConfig = async (
	token: string
): Promise<{
	VISION_SUPPORT_MODEL: string;
	VISION_SYSTEM_PROMPT: string;
}> => {
	let error = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/rag/vision`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `getRagVisionConfig: ${err}`;
			console.log(error);
			return null;
		});

	if (error) {
		throw error;
	}

	return {
		VISION_SUPPORT_MODEL: res?.VISION_SUPPORT_MODEL ?? '',
		VISION_SYSTEM_PROMPT: res?.VISION_SYSTEM_PROMPT ?? ''
	};
};

export const setRagVisionConfig = async (
	token: string,
	config: { VISION_SUPPORT_MODEL: string; VISION_SYSTEM_PROMPT: string }
): Promise<{ VISION_SUPPORT_MODEL: string; VISION_SYSTEM_PROMPT: string }> => {
	let error = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/rag/vision`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			VISION_SUPPORT_MODEL: config.VISION_SUPPORT_MODEL,
			VISION_SYSTEM_PROMPT: config.VISION_SYSTEM_PROMPT
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `setRagVisionConfig: ${err}`;
			console.log(error);
			return null;
		});

	if (error) {
		throw error;
	}

	return {
		VISION_SUPPORT_MODEL: res?.VISION_SUPPORT_MODEL ?? config.VISION_SUPPORT_MODEL,
		VISION_SYSTEM_PROMPT: res?.VISION_SYSTEM_PROMPT ?? config.VISION_SYSTEM_PROMPT
	};
};

// --- API Tools ---

export const getApiToolsConfig = async (
	token: string
): Promise<{
	enabled: boolean;
	allowed_categories: string[];
	allow_tool_servers: boolean;
}> => {
	let error = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/api-tools`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `getApiToolsConfig: ${err}`;
			console.log(error);
			return null;
		});

	if (error) {
		throw error;
	}

	return {
		enabled: res?.enabled ?? false,
		allowed_categories: res?.allowed_categories ?? ['time', 'knowledge', 'web_search'],
		allow_tool_servers: res?.allow_tool_servers ?? false
	};
};

export const setApiToolsConfig = async (
	token: string,
	config: { enabled: boolean; allowed_categories: string[]; allow_tool_servers: boolean }
): Promise<{ enabled: boolean; allowed_categories: string[]; allow_tool_servers: boolean }> => {
	let error = null;
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/api-tools`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			enabled: config.enabled,
			allowed_categories: config.allowed_categories,
			allow_tool_servers: config.allow_tool_servers
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `setApiToolsConfig: ${err}`;
			console.log(error);
			return null;
		});

	if (error) {
		throw error;
	}

	return {
		enabled: res?.enabled ?? config.enabled,
		allowed_categories: res?.allowed_categories ?? config.allowed_categories,
		allow_tool_servers: res?.allow_tool_servers ?? config.allow_tool_servers
	};
};

// --- Subagents ---

export const getSubagentsConfig = async (token: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/subagents`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	});
	if (!res.ok) throw await res.json();
	return res.json();
};

export const setSubagentsConfig = async (token: string, config: object) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/subagents`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(config)
	});
	if (!res.ok) throw await res.json();
	return res.json();
};

export const setDefaultPromptSuggestions = async (token: string, promptSuggestions: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/suggestions`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			suggestions: promptSuggestions
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getBanners = async (token: string): Promise<Banner[]> => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/banners`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const setBanners = async (token: string, banners: Banner[]) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/banners`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			banners: banners
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};
