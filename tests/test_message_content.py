"""Message.content 数组、请求视图 helper 与图片 token 估算。"""
from core.context.message import (
    Message,
    content_text,
    is_injected_content,
    is_synthetic_content,
    to_llm_messages,
)
from core.context.token import IMAGE_TOKEN_ESTIMATE, estimate_size, estimate_token
from core.template import (
    IMAGE_OMITTED_MESSAGE,
    INJECTION_MESSAGES_TEMPLATE,
    SKILL_INVOCATION_TEMPLATE,
)


def _image_content(text="images"):
    return [
        {"type": "text", "text": INJECTION_MESSAGES_TEMPLATE.format(content=text)},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]


def test_content_text_flattens_text_parts():
    assert content_text("plain") == "plain"
    assert content_text(_image_content("a")) == INJECTION_MESSAGES_TEMPLATE.format(content="a")
    assert content_text([
        {"type": "text", "text": "a"},
        {"type": "input_audio", "input_audio": {"data": "x"}},
        {"type": "text", "text": "b"},
    ]) == "a\nb"


def test_injected_and_synthetic_detection():
    content = _image_content()
    assert is_injected_content(content)
    assert is_synthetic_content(content)
    assert is_synthetic_content("<compacted_messages>\nsummary\n</compacted_messages>")
    assert is_synthetic_content("[Fork] x")
    assert not is_injected_content("hello")
    assert not is_synthetic_content([{"type": "text", "text": "hello"}])


def test_skill_invocation_is_user_content_not_synthetic():
    invocation = SKILL_INVOCATION_TEMPLATE.format(name="review")
    assert invocation == "[Invoke skill 'review']"
    assert not is_synthetic_content(invocation)
    assert not is_synthetic_content(invocation + " check auth"), "补充信息不应改变技能调用的用户消息身份"
    assert not is_synthetic_content("\n" + invocation)


def test_llm_view_passes_arrays_through():
    message = Message(role="user", content=_image_content())
    assert to_llm_messages([message]) == [{"role": "user", "content": message.content}]


def test_llm_view_replaces_whole_image_message_without_vision():
    message = Message(role="user", content=_image_content())
    assert to_llm_messages([message], vision=False) == [
        {"role": "user", "content": IMAGE_OMITTED_MESSAGE},
    ]


def test_llm_view_keeps_text_messages_without_vision():
    assert to_llm_messages([{"role": "user", "content": "hi"}], vision=False) == [
        {"role": "user", "content": "hi"},
    ]


def test_estimate_size_counts_image_parts_as_1000_tokens():
    text_only = [{"role": "user", "content": "hello"}]
    with_image = [{
        "role": "user",
        "content": [{"type": "text", "text": "hello"}, *_image_content()[1:]],
    }]
    assert IMAGE_TOKEN_ESTIMATE == 1000
    assert estimate_size(with_image) == estimate_size(text_only) + IMAGE_TOKEN_ESTIMATE


def test_estimate_size_handles_message_objects_with_images():
    text = INJECTION_MESSAGES_TEMPLATE.format(content="a")
    message = Message(role="user", content=_image_content("a"))
    assert estimate_size([message]) == estimate_token(text) + IMAGE_TOKEN_ESTIMATE


def test_llm_view_copies_array_content():
    message = Message(role="user", content=_image_content())
    view = to_llm_messages([message])
    view[0]["content"][0]["text"] = "mutated"
    assert message.content[0]["text"] == INJECTION_MESSAGES_TEMPLATE.format(content="images")


def test_llm_view_without_vision_does_not_mutate_and_recovers():
    message = Message(role="user", content=_image_content())
    to_llm_messages([message], vision=False)
    assert message.content == _image_content()
    assert to_llm_messages([message], vision=True)[0]["content"][1]["type"] == "image_url"


def test_split_keeps_image_message_in_suffix():
    from core.context.compact.context_compact import find_index_to_split

    messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "a"},
        Message(role="user", content=_image_content()),
    ]
    reserve = estimate_size([messages[2]])
    assert find_index_to_split(messages, reserve) == 2
    assert messages[2].content == _image_content()
