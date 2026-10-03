"""Gemini function declarations, shared by indexed and baseline runs."""
def tool(name, description, properties=None, required=None):
    return {'name':name,'description':description,'parameters':{'type':'OBJECT',
        'properties':properties or {},'required':required or []}}
def string(description):
    return {'type':'STRING','description':description}
def integer(description):
    return {'type':'INTEGER','description':description}

READ_TOOLS=[
 tool('search_files','Keyword search over indexed text. Returns ranked excerpts and file IDs.',
      {'query':string('Search keywords'),'limit':integer('1 to 20; default 5')},['query']),
 tool('read_file','Read current UTF-8 content by stable ID. Offsets and lengths are bytes.',
      {'file_id':string('ID returned by search or list'),'offset':integer('Start byte, default 0'),
       'length':integer('Up to 32768 bytes, default 8192')},['file_id']),
 tool('list_files','List at most 200 indexed files with IDs, paths, hashes and short descriptions.',
      {'limit':integer('Maximum entries, default 200')}),
 tool('run_program','Execute a registered read-only utility, without a shell.',
      {'program':string('system_info, disk_usage, or memory_usage')},['program']),
 tool('system_status','Read Linux architecture, load and workspace disk usage.'),
 tool('history','Inspect recent file operation IDs and recovery status.',{'limit':integer('Default 20')})]
WRITE_TOOLS=[
 tool('move_file','Move one file within the workspace. Never overwrites. Requires local user approval.',
      {'file_id':string('File ID'),'destination':string('Relative destination path'),
       'expected_hash':string('SHA256 returned by a current read or search')},['file_id','destination','expected_hash']),
 tool('write_file','Create or replace a supported UTF-8 document, after local approval. Existing files need current hash.',
      {'path':string('Relative .txt/.md/.csv/.json path'),'content':string('Complete new text'),
       'expected_hash':string('Current SHA256; empty string when creating')},['path','content','expected_hash']),
 tool('undo','Undo a recorded move or replacement if files have not changed. New-file deletion is unsupported.',
      {'operation_id':string('ID from history')},['operation_id'])]

CONTEXT_TOOLS=[
 tool('find_documents','Find documents using local metadata and text, filtered by human viewing activity and calendar time.',
 {'query':string('Topic words'),'file_type':string('Optional extension such as pdf'),'activity':string('human_view or empty'),
 'time_range':string('yesterday, today, last_week, or empty'),'timezone':string('User IANA timezone'),'limit':integer('Maximum 20')},['query']),
 tool('related_files','Find other documents with an explicitly supported shared project label.',{'file_id':string('File ID')},['file_id']),
 tool('timeline','Read human document-opening events with timestamps.',{'limit':integer('Maximum 100')})
]
