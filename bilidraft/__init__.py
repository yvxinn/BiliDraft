"""bilidraft：B站视频 → 无损图文讲义

管线：acquire → transcribe → polish → structure → slides → align → export
每阶段幂等落盘于 notes/<bvid>/.task/，支持断点续跑。
"""

__version__ = "0.3.0"
