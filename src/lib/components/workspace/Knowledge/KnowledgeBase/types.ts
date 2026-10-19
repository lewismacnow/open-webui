export type KnowledgeFile = {
	id?: string;
	itemId?: string;
	filename?: string;
	name?: string;
	directory_id?: string | null;
	directory_path?: string;
	has_original?: boolean;
	status?: string;
	updated_at?: number;
	meta?: {
		name?: string;
		title?: string;
		size?: number;
		content_type?: string;
		ignored?: boolean;
		data?: { directory_id?: string | null };
		[key: string]: unknown;
	};
	data?: { status?: string; error?: string };
	// Fork: durable embedding pipeline state from knowledge_file.status
	embedding_status?: string | null;
	embedding_error?: string | null;
	user?: { name?: string; email?: string };
};
export type KnowledgeDirectory = {
	id: string;
	name: string;
	parent_id: string | null;
	updated_at?: number;
};
export type ViewerState = {
	mode: 'preview' | 'indexed';
	indexed: string | null;
	draft: string;
	editing: boolean;
	originalUnavailable?: boolean;
};
export const fileName = (file: KnowledgeFile) =>
	file.meta?.title || file.meta?.name || file.name || file.filename || 'Untitled';
export const newViewerState = (): ViewerState => ({
	mode: 'preview',
	indexed: null,
	draft: '',
	editing: false
});
