"""Frozen, gated worker owner that survives its detached coordinator."""
import argparse, hashlib, json, os, signal, subprocess, threading, time
from pathlib import Path
from .store import Store
from .supervisor import process_start_identity, inspect_process, _live_group_exists

def _atomic(path: Path, value):
    temporary=path.with_name(path.name+f".tmp.{os.getpid()}")
    with temporary.open("w") as stream:
        stream.write(json.dumps(value,sort_keys=True)+"\n"); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary,path)
    directory=os.open(path.parent,os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)

def _drain(stream, capture: Path, limit: int, result: dict):
    digest=hashlib.sha256(); total=0; kept=0
    with capture.open("wb") as output:
        while True:
            chunk=stream.read(65536)
            if not chunk: break
            total+=len(chunk); digest.update(chunk); remaining=limit-kept
            if remaining>0: output.write(chunk[:remaining]); kept+=min(remaining,len(chunk))
        output.flush(); os.fsync(output.fileno())
    result.update(total_bytes=total,captured_bytes=kept,truncated=total>kept,full_sha256=digest.hexdigest(),captured_sha256=hashlib.sha256(capture.read_bytes()).hexdigest())

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--gate-fd",type=int,required=True); p.add_argument("--database",type=Path,required=True); p.add_argument("--artifacts",type=Path,required=True); p.add_argument("--run-id",required=True); p.add_argument("--attempt-token",required=True); p.add_argument("--supervisor-token",type=int,required=True); p.add_argument("--stdout",type=Path,required=True); p.add_argument("--stderr",type=Path,required=True); p.add_argument("--exit-record",type=Path,required=True); p.add_argument("--child-record",type=Path,required=True); p.add_argument("--limit",type=int,required=True); p.add_argument("--timeout",type=float,required=True); p.add_argument("--grace",type=float,required=True); p.add_argument("command",nargs=argparse.REMAINDER)
    a=p.parse_args(argv); command=a.command[1:] if a.command[:1]==["--"] else a.command
    with os.fdopen(a.gate_fd,"rb",closefd=True) as gate:
        if gate.read(1)!=b"1": return 125
    child_gate_read,child_gate_write=os.pipe()
    gated=[os.sys.executable,"-m","devsquad.worker_gate","--gate-fd",str(child_gate_read),"--",*command]
    child=subprocess.Popen(gated,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,pass_fds=(child_gate_read,))
    os.close(child_gate_read)
    started=process_start_identity(child.pid)
    if started is None:
        child.kill(); child.wait(); return 126
    _atomic(a.child_record,{"pid":child.pid,"pgid":child.pid,"process_start_id":started})
    os.write(child_gate_write,b"1"); os.close(child_gate_write)
    out,err={},{}; threads=[threading.Thread(target=_drain,args=(child.stdout,a.stdout,a.limit,out)),threading.Thread(target=_drain,args=(child.stderr,a.stderr,a.limit,err))]
    for thread in threads: thread.start()
    store=Store(a.database,a.artifacts); cancelled=False; timed_out=False; deadline=time.monotonic()+a.timeout
    try:
        while child.poll() is None:
            run=store.run(a.run_id)
            if run["state"]=="cancelling" or time.monotonic()>=deadline:
                cancelled=run["state"]=="cancelling"; timed_out=not cancelled
                if inspect_process(child.pid,child.pid,started)!="live": raise RuntimeError("child identity became unsafe")
                try: os.killpg(child.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                try: child.wait(timeout=a.grace)
                except subprocess.TimeoutExpired:
                    try: os.killpg(child.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    child.wait()
                break
            store.heartbeat_attempt(a.run_id,a.attempt_token,a.supervisor_token); time.sleep(.1)
        returncode=child.wait()
    finally: store.close()
    if _live_group_exists(child.pid):
        try: os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        cleanup_deadline=time.monotonic()+a.grace
        while _live_group_exists(child.pid) and time.monotonic()<cleanup_deadline: time.sleep(.05)
        if _live_group_exists(child.pid): raise RuntimeError("worker process group survived cleanup")
    for thread in threads: thread.join(timeout=a.grace+1)
    if any(thread.is_alive() for thread in threads): raise RuntimeError("durable output drain did not finish")
    child.stdout.close(); child.stderr.close()
    _atomic(a.exit_record,{"returncode":returncode,"cancelled":cancelled,"timed_out":timed_out,"stdout":out,"stderr":err,"finished_at":time.time()})
    return returncode
if __name__=="__main__": raise SystemExit(main())
