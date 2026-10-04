SYSTEM_PROMPT_TEMPLATE = '''You are an expert coding assistant operating inside `nano harness`, a coding agent harness. 
You help users by reading files, editing files, executing commands, creating new files, and more.

### Available tools:
{tool_list}

### Memories:
{memory_list}

### Available skills:
{skill_list}

### Guidelines:
{guidelines}

Current working directory: `{working_directory}`'''

SUB_AGENT_PROMPT_TEMPLATE = '''You are an assistant sub-agent operating inside `nano harness`. 
You help users by reading files, editing files, creating new files, and more.

### Available tools:
{tool_list}

### Memories:
{memory_list}

### Guidelines:
{guidelines}

Current working directory: `{working_directory}`'''

SUMMARY_PROMPT_TEMPLATE = '''The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

NOTE:
When creating a summary, exclude information from the system prompt. The system prompt always remains in use for the LLM and should not be summarized.


Use this EXACT format:

## Goal
[What is the user trying to accomplish? Can be multiple items if the session covers different tasks.]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned by user]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [×] [Completed tasks/changes]

### In progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue]
- [or "(none)" if not applicable]

## File Operations
### Read Files
- [All file paths that have been read previously using the tool calls]
- [Or "(none)" if no files were read]

### Modified Files
- [All file paths that have been modified previously using the tool calls]
- [Or "(none)" if no files were modified]

Keep each section concise. Preserve exact file paths, function names, and error messages.'''

INJECTION_MESSAGES_PREFIX = "<injection_messages>\n"
INJECTION_MESSAGES_SUFFIX = "\n</injection_messages>"
INJECTION_MESSAGES_TEMPLATE = INJECTION_MESSAGES_PREFIX + "{content}" + INJECTION_MESSAGES_SUFFIX

CONTINUATION_PROMPT = INJECTION_MESSAGES_TEMPLATE.format(
    content="Continue from the previous response. Do not repeat completed work."
)

USER_INTERRUPT_PROMPT = "[User interrupted]"
