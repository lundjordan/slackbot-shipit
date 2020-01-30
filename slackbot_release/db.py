import collections
from contextlib import contextmanager
import logging

from sqlalchemy import Column, String, Integer, ForeignKey, Boolean
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.sql import not_


from slackbot_release.shipit import get_shipit_releases

### logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
LOGGER = logging.getLogger(__name__)

#### db setup
engine = create_engine("sqlite:///slackbot_release.db", echo=True)
Session = sessionmaker(bind=engine)
Base = declarative_base()

def create_db():
    Base.metadata.create_all(engine)

@contextmanager
def session_scope():
    "use context to manage session lifecycle in transactions"
    session = Session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()


class Release(Base):
    __tablename__ = "releases"

    name = Column(String, primary_key=True)
    product = Column(String)
    version = Column(String)
    repo = Column(String)
    revision = Column(String)
    phases = relationship("Phase", cascade="all, delete-orphan")
    slack_threads = relationship("SlackThread", cascade="all, delete-orphan")


class Phase(Base):
    __tablename__ = "phases"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    groupid = Column(String)
    # triggered tracks if phase was initiated in shipit
    triggered = Column(Boolean)
    done = Column(Boolean)
    release_id = Column(String, ForeignKey("releases.name"))


class SlackThread(Base):
    __tablename__ = "slack_threads"

    threadid = Column(String, primary_key=True)
    tasks = relationship("Task", cascade="all, delete-orphan")
    release_id = Column(String, ForeignKey("releases.name"))


class Task(Base):
    __tablename__ = "tasks"

    taskid = Column(String, primary_key=True)
    thread_id = Column(String, ForeignKey("slack_threads.threadid"))

NamedRelease = collections.namedtuple('Release', 'name, product, version, repo, revision, phases, slack_threads')
NamedPhase = collections.namedtuple('Phase', 'name, groupid, triggered, done')
NamedSlackThread = collections.namedtuple('SlackThread', 'threadid, tasks')
NamedTask = collections.namedtuple('SlackThread', 'taskid, threadid')


def task_tracked(task, release_name):
    with session_scope() as session:
        release = session.query(Release).get(release_name)
        for thread in release.slack_threads:
            if task in [t.taskid for t in thread.tasks]:
                return True
        return False

def track_slack_thread(threadid, tasks, release_name):
    with session_scope() as session:
        release = session.query(Release).get(release_name)
        thread = SlackThread(threadid=threadid)
        thread.tasks = [Task(taskid=taskid) for taskid in tasks]
        release.slack_threads.append(thread)

def update_tasks_in_thread(threadid, tasks):
    with session_scope() as session:
        thread = session.query(SlackThread).get(threadid)
        thread.tasks = [Task(taskid=taskid) for taskid in tasks]

def mark_phase_as_done(phase_name, release_name):
    with session_scope() as session:
        release = session.query(Release).get(release_name)
        target_phase = next(phase for phase in release.phases if phase.name == phase_name)
        target_phase.done = True

def add_release(shipit_release):
    with session_scope() as session:
        new_release = Release(
            name=shipit_release["name"],
            product=shipit_release["product"],
            version=shipit_release["version"],
            repo=shipit_release["project"],
            revision=shipit_release["revision"]
        )
        phases = []
        for shipit_phase in shipit_release["phases"]:
            phases.append(Phase(
                name=shipit_phase["name"],
                groupid=shipit_phase["actionTaskId"],
                triggered=True if shipit_phase["completed"] else False,
                done=False  # done tracks if TC graph is complete
            ))
        new_release.phases = phases
        session.add(new_release)
        return new_release

def update_phases(shipit_release):
    with session_scope() as session:
        release = session.query(Release).get(shipit_release["name"])
        for phase in release.phases:
            # get corresponding shipit phase which holds live state
            shipit_phase = next(i for i in shipit_release["phases"] if i["name"] == phase.name)
            # update phase live state
            phase.groupid = shipit_phase["actionTaskId"]
            phase.triggered = True if shipit_phase["completed"] else False

def delete_old_threads(release_name):
    with session_scope() as session:
        session.query(SlackThread).filter(not_(SlackThread.tasks.any())).delete(synchronize_session='fetch')

def delete_old_releases(shipit_releases):
    release_names = [r["name"] for r in shipit_releases]
    with session_scope() as session:
        for release in session.query(Release).all():
            if release.name not in release_names:
                session.delete(release)

def get_releases():
    releases = []
    with session_scope() as session:
        for release in session.query(Release).all():
            r = NamedRelease(name=release.name,
                             product=release.product,
                             version=release.version,
                             repo=release.repo,
                             revision=release.revision,
                             phases=[],
                             slack_threads=[])
            for phase in release.phases:
                r.phases.append(NamedPhase(name=phase.name,
                                groupid=phase.groupid,
                                triggered=phase.triggered,
                                done=phase.done))
            for thread in release.slack_threads:
                r.slack_threads.append(
                    NamedSlackThread(threadid=thread.threadid, tasks=[t.taskid for t in thread.tasks])
                )
            releases.append(r)
        return releases

def get_release(name):
    with session_scope() as session:
        return session.query(Release).get(name)

async def update_releases(config, logger=LOGGER):
    shipit_releases = await get_shipit_releases(config)

    # delete old tracked releases no longer in shipit
    delete_old_releases(shipit_releases)

    for shipit_release in shipit_releases:
        if not get_release(shipit_release["name"]):
            add_release(shipit_release)
        else:
            update_phases(shipit_release)

    return get_releases()
