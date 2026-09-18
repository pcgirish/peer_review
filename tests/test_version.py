from review_loop.version import plan_version, pr_version


def test_plan_version_is_content_plus_intent_hash():
    a = plan_version("plan", "intent")
    assert a == plan_version("plan", "intent")
    assert a != plan_version("plan2", "intent")
    assert a != plan_version("plan", "intent2")
    content, intent = a.split("+")
    assert len(content) == 12 and len(intent) == 12


def test_pr_version_is_head_sha_plus_intent_hash():
    v = pr_version("0123456789abcdef", "intent")
    assert v.startswith("0123456789abcdef+")
    assert v != pr_version("0123456789abcdef", "other")
