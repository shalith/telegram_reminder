from app.learning.self_learning import SelfLearningEngine
from app.models import Base, PhraseRiskScore
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_record_confirmation_initializes_defaults_for_new_rows():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    learning = SelfLearningEngine()
    with Session() as session:
        learning.record_confirmation(session, telegram_user_id=1, signature="wake me up <time>", confirmed=True)
        row = session.query(PhraseRiskScore).one()
        assert row.confirmed_count == 1
        assert row.success_count == 1
        assert row.risk_level >= 0.0
