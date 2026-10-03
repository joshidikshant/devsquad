"""Execute an internal worker only after its supervisor persists ownership."""
import argparse, os

def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--gate-fd",type=int,required=True); parser.add_argument("command",nargs=argparse.REMAINDER)
    args=parser.parse_args(argv); command=args.command[1:] if args.command[:1]==["--"] else args.command
    with os.fdopen(args.gate_fd,"rb",closefd=True) as gate:
        if gate.read(1)!=b"1": return 125
    os.execvpe(command[0],command,os.environ)

if __name__=="__main__": raise SystemExit(main())
