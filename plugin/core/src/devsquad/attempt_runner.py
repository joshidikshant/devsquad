"""Frozen, gated worker owner that survives its detached coordinator."""
import argparse, hashlib, json, os, signal, subprocess, threading, time
from pathlib import Path
from .store import Store
from .supervisor import process_start_identity

def _atomic(path: Path, value):
    temporary=path.with_name(path.name+f".tmp.{os.getpid()}"); temporary.write_text(json.dumps(value,sort_keys=True)+"\n"); os.replace(temporary,path)

def _drain(stream, capture: Path, limit: int, result: dict):
    digest=hashlib.sha256(); total=0; kept=0
    with capture.open("wb") as output:
        while True:
            chunk=stream.read(65536)
            if not chunk: break
            total+=len(chunk); digest.update(chunk); remaining=limit-kept
            if remaining>0: output.write(chunk[:remaining]); kept+=min(remaining,len(chunk))
        output.flush(); os.fsync(output.fileno())
    result.update(total_bytes=total,captured_bytes=kept,truncated=total>kept,full_sha256=digest.hexdigest())

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--gate-fd",type=int,required=True); p.add_argument("--database",type=Path,required=True); p.add_argument("--artifacts",type=Path,required=True); p.add_argument("--run-id",required=True); p.add_argument("--attempt-token",required=True); p.add_argument("--supervisor-token",type=int,required=True); p.add_argument("--stdout",type=Path,required=True); p.add_argument("--stderr",type=Path,required=True); p.add_argument("--exit-record",type=Path,required=True); p.add_argument("--child-record",type=Path,required=True); p.add_argument("--limit",type=int,required=True); p.add_argument("command",nargs=argparse.REMAINDER)
    a=p.parse_args(argv); command=a.command[1:] if a.command[:1]==["--"] else a.command
    with os.fdopen(a.gate_fd,"rb",closefd=True) as gate:
        if gate.read(1)!=b"1": return 125
    child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    started=process_start_identity(child.pid)
    if started is None:
        child.kill(); child.wait(); return 126
    _atomic(a.child_record,{"pid":child.pid,"pgid":child.pid,"process_start_id":started})
    out,err={},{}; threads=[threading.Thread(target=_drain,args=(child.stdout,a.stdout,a.limit,out)),threading.Thread(target=_drain,args=(child.stderr,a.stderr,a.limit,err))]
    for thread in threads: thread.start()
    store=Store(a.database,a.artifacts); cancelled=False
    try:
        while child.poll() is None:
            run=store.run(a.run_id)
            if run["state"]=="cancelling":
                cancelled=True
                try: os.killpg(child.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try: os.killpg(child.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                break
            store.heartbeat_attempt(a.run_id,a.attempt_token,a.supervisor_token); time.sleep(.1)
        returncode=child.wait()
    finally: store.close()
    for thread in threads: thread.join()
    _atomic(a.exit_record,{"returncode":returncode,"cancelled":cancelled,"stdout":out,"stderr":err,"finished_at":time.time()})
    return returncode
if __name__=="__main__": raise SystemExit(main())
