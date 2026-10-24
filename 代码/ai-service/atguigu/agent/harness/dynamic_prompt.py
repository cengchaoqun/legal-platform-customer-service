import json

from langchain.agents.middleware import ModelRequest, dynamic_prompt

from atguigu.agent.harness.rules.action import ACTION_CATALOG
from atguigu.agent.harness.skills.catalog import SKILL_CATALOG
from atguigu.agent.harness.skills.definition import SkillCode
from atguigu.agent.llm.prompt import BASE_PROMPT


def render_skill_prompt(active_skill_code: SkillCode | None) -> str:
    """根据当前 Skill 生成领域处理说明。"""
    if active_skill_code is None:
        return (
            "【动态 Skill 路由】\n"
            "当前 Run 尚未指定领域技能，先用 load_skill 加载与用户问题匹配的技能。\n"
            f"可用 Skill：\n{SKILL_CATALOG.render_index()}"
        )

    skill = SKILL_CATALOG.get_skill(active_skill_code)
    guidance = "\n".join(f"- {item}" for item in skill.guidance)
    return (
        f"【当前 Skill：{skill.code}】\n"
        f"{skill.description}\n"
        f"{guidance}\n"
        f"允许工具：{', '.join(skill.tools)}"
    )


def render_plan_prompt(plan: dict | None) -> str:
    """渲染服务端执行计划，明确本轮节点、槽位与允许动作。"""
    if not plan:
        return ""
    payload = json.dumps(plan, ensure_ascii=False, indent=2)
    return (
        "【服务端执行计划（只读，不可修改）】\n"
        f"{payload}\n"
        "要求：\n"
        "1. 只能调用 allowed_tools 中的工具，且必须为当前节点获取必要证据。\n"
        "2. 只能输出 allowed_next_actions 中的回复类型；澄清时只问 clarify_slot 对应的一个问题。\n"
        "3. 需要页面动作时只能使用 allowed_action_codes 中的编码，禁止构造 URL。\n"
    )


def render_support_prompt(
    active_skill_code: SkillCode | None,
    plan: dict | None = None,
) -> str:
    skill_prompt = render_skill_prompt(active_skill_code)
    action_prompt = f"【白名单页面动作】\n{ACTION_CATALOG.render_action_index()}"
    return (
        f"{BASE_PROMPT}\n\n{skill_prompt}\n\n"
        f"{render_plan_prompt(plan)}\n\n{action_prompt}"
    )


@dynamic_prompt
def support_prompt(request: ModelRequest) -> str:
    """为每次模型调用生成当前能力范围内的系统提示词。"""
    state = request.state
    skill_code = state.get("active_skill_code") or state.get("selected_skill")
    if isinstance(skill_code, str):
        try:
            skill_code = SkillCode(skill_code)
        except ValueError:
            skill_code = None
    return render_support_prompt(skill_code, state.get("execution_plan"))
