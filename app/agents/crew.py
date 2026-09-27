"""文本链路 Crew：卖点挖掘 → 俄语 Listing，串行执行。"""
from crewai import Agent, Crew, LLM, Process, Task

from ..config import settings
from ..llm import extract_json


def _llm() -> LLM:
    # CrewAI 通过 litellm 走 OpenAI 兼容端点，DashScope 与百炼其他模型可直接替换
    return LLM(
        model=f"openai/{settings.qwen_text_model}",
        base_url=settings.dashscope_base_url,
        api_key=settings.dashscope_api_key,
        temperature=0.7,
    )


def run_text_crew(vision: dict, purchase_price: float, weight_g: int) -> tuple[list, dict]:
    """输入图像解析结果，返回 (卖点列表, 俄语Listing)。"""
    selling_agent = Agent(
        role="Ozon 卖点挖掘专家",
        goal="站在俄罗斯 Ozon 买家视角，提炼能促成下单的商品卖点",
        backstory="你深耕俄罗斯电商多年，熟悉 Ozon 买家的关注点、消费习惯和常见退货原因。",
        llm=_llm(),
        verbose=False,
    )
    listing_agent = Agent(
        role="Ozon 俄语 Listing 文案专家",
        goal="产出符合 Ozon 规范、可直接上架的俄语商品资料",
        backstory="母语级俄语文案，熟悉 Ozon 搜索排序规则和类目属性填写规范。",
        llm=_llm(),
        verbose=False,
    )

    selling_task = Task(
        description=(
            f"商品图像解析结果：{vision}\n"
            f"采购价：{purchase_price} 元，重量：{weight_g} 克。\n"
            "请提炼 5-8 条卖点，每条包含：中文标题、一句俄语卖点短句、支撑理由。"
            "只输出 JSON 数组，字段：title_cn, sentence_ru, reason。"
        ),
        expected_output="JSON 数组：[{title_cn, sentence_ru, reason}, ...]",
        agent=selling_agent,
    )
    listing_task = Task(
        description=(
            f"基于图像解析结果 {vision} 和上面提炼的卖点，生成 Ozon 上架资料。\n"
            "要求：标题不超过 200 字符；描述自然融入卖点；关键词 8-12 个；"
            "类目属性给出建议键值对。只输出 JSON。"
        ),
        expected_output=(
            "JSON 对象：{title_ru, description_ru, keywords_ru: [], "
            "category_suggestion, attributes: {}}"
        ),
        agent=listing_agent,
        context=[selling_task],
    )

    crew = Crew(
        agents=[selling_agent, listing_agent],
        tasks=[selling_task, listing_task],
        process=Process.sequential,
        verbose=False,
    )
    crew.kickoff()

    selling = extract_json(selling_task.output.raw)
    listing = extract_json(listing_task.output.raw)
    return selling, listing
