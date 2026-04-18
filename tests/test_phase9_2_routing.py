from app.phase9_2 import ToolFirstRouter, ReferenceResolver, ReferenceContext


def test_tool_router_maps_agenda_queries_to_list():
    router = ToolFirstRouter()
    assert router.detect('What are my existing agenda').kind == 'list_all'
    assert router.detect('List today\'s reminder').kind == 'list_today'
    assert router.detect('List tomorrow reminders').kind == 'list_tomorrow'


def test_reference_resolver_substitutes_pronoun_with_last_task_and_time():
    resolver = ReferenceResolver()
    ctx = ReferenceContext(last_discussed_task='go to church', last_discussed_time_phrase='tomorrow morning')
    rewritten = resolver.substitute_pronoun_create('Remind me it', ctx)
    assert 'go to church' in rewritten.lower()
    assert 'tomorrow morning' in rewritten.lower()


def test_reference_resolver_builds_update_rewrite_for_ordinal():
    resolver = ReferenceResolver()
    assert resolver.extract_target_id('Update the 2nd reminder as go to church', [5, 9, 12]) == 9
    rewritten = resolver.build_update_rewrite('2nd reminder is go to church', 9)
    assert rewritten.lower() == 'update #9 to go to church'
