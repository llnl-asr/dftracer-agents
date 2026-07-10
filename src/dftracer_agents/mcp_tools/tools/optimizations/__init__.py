from .diagnose import register_diagnose_tools
from .iteration import register_iteration_tools
from .levels import register_level_tools
from .memory import register_memory_tools
from .orchestrator import register_orchestrator_tools
from .knowledge_base import register_optimization_kb_tools
from .context_search import register_context_search_tools


def register_optimization_tools(mcp) -> None:
    register_diagnose_tools(mcp)
    register_iteration_tools(mcp)
    register_level_tools(mcp)
    register_memory_tools(mcp)
    register_orchestrator_tools(mcp)
    register_optimization_kb_tools(mcp)
    register_context_search_tools(mcp)
