# agent 包：智能体装配层
# 导出：按 §3.4.3 / §4.1 的 import 契约，符号均从子模块导入：
#   from agent.llm import model
#   from agent.load_prompts import main_agent_config, sub_agents_config
#   from agent.main_agent import main_agent, run_deep_agent
#   from agent.sub_agents.<rag_sub_agent|db_sub_agent|network_search_agent> import <子智能体>
# 此处不做急切导入，避免 prompts.yml / .env / tools 缺失时阻断 `import agent.*` 子模块
