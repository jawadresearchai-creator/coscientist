"""The queue is what makes the LLMs optional."""
import pytest

from coscientist.tickets import Agent, Policy, TicketQueue, TicketState, TicketType


def queue(tmp_path):
    return TicketQueue(str(tmp_path / "tickets"))


def test_emitting_a_ticket_does_not_block_the_core(tmp_path):
    q = queue(tmp_path)
    t = q.emit(type=TicketType.NOVELTY_VERDICT, agent=Agent.CLAUDE,
               subject="C900 novelty residual", question="Does the residual clear the bar?",
               default_action="proceed to slate with novelty marked UNVERIFIED")
    assert t.state is TicketState.OPEN
    assert not t.blocking


def test_pre_freeze_audit_is_the_only_blocking_type(tmp_path):
    q = queue(tmp_path)
    blocking = q.emit(type=TicketType.PRE_FREEZE_AUDIT, agent=Agent.CLAUDE,
                      subject="C900 freeze", question="Is this design sound?",
                      default_action="do not freeze")
    other = q.emit(type=TicketType.MANUSCRIPT_PROSE, agent=Agent.CLAUDE,
                   subject="C900 prose", question="Write it",
                   default_action="ship MANUSCRIPT_SKELETON")
    assert blocking.blocking and not other.blocking
    assert [t.id for t in q.blocking_open()] == [blocking.id]


def test_a_blocking_ticket_has_no_safe_default(tmp_path):
    q = queue(tmp_path)
    t = q.emit(type=TicketType.PRE_FREEZE_AUDIT, agent=Agent.CLAUDE, subject="s",
               question="q", default_action="do not freeze")
    with pytest.raises(RuntimeError, match="no safe default"):
        q.apply_default(t.id)


def test_non_blocking_tickets_default_and_the_core_moves_on(tmp_path):
    q = queue(tmp_path)
    t = q.emit(type=TicketType.MANUSCRIPT_PROSE, agent=Agent.CLAUDE, subject="s",
               question="q", default_action="ship skeleton")
    assert q.apply_default(t.id).state is TicketState.DEFAULTED
    assert q.open_tickets() == []


def test_quota_death_mid_repair_leaves_a_resumable_ticket(tmp_path):
    """Codex dies mid-repair: progress is kept, nothing is corrupted."""
    q = queue(tmp_path)
    t = q.emit(type=TicketType.BUILD_REPAIR, agent=Agent.CODEX, subject="panel build fails",
               question="make test_panel_build pass", default_action="keep last good commit")
    q.mark_partial(t.id, {"commit": "abc123", "tests_passing": 3, "tests_failing": 1})
    again = q.get(t.id)
    assert again.state is TicketState.PARTIAL
    assert again.context["partial_progress"]["commit"] == "abc123"
    assert again in q.open_tickets() or any(x.id == t.id for x in q.open_tickets())


def test_policy_routes_tickets_without_asking_forty_times(tmp_path):
    q = queue(tmp_path)
    q.emit(type=TicketType.BUILD_REPAIR, agent=Agent.CODEX, subject="repair",
           question="q", default_action="keep last good commit")
    q.emit(type=TicketType.REVIEWER_COURT, agent=Agent.CLAUDE, subject="court",
           question="q", default_action="ship without internal review")
    policy = Policy({"build_repair": "auto", "reviewer_court": "ask"}, default="ask")
    digest = q.digest(policy)
    assert len(digest["auto"]) == 1 and len(digest["ask"]) == 1


def test_policy_rejects_nonsense_values():
    with pytest.raises(ValueError):
        Policy({"build_repair": "sometimes"})
