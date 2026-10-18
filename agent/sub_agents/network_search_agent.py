from agent.load_prompts import sub_agents_config
from tools.internet_search_tool import internet_search

# 网络请求subagent
internet_sub_agent ={
    "name": sub_agents_config['tavily']['name'],
    "description":sub_agents_config['tavily']['description'],
    "tools": [internet_search],
    "system_prompt":sub_agents_config['tavily']['system_prompt']
}


if __name__ == "__main__":
    print(internet_sub_agent) # 从yml 把数据正确的读取过来
