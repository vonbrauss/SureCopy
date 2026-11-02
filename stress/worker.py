import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from surecopy.store import Store
from surecopy.engine import Engine
from surecopy.jobs import resume_job
db, job = sys.argv[1], int(sys.argv[2])
store = Store(db)
resume_job(store, job)
print(Engine(store, checkpoint_bytes=int(sys.argv[3])).run(job).value, flush=True)
