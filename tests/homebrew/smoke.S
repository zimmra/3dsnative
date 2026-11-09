/* Original game-independent ARM11 smoke program. No SDK or game assets. */
.syntax unified
.arm
.arch armv6k
.section .text
.global _start
_start:
    adr r0, message
    mov r1, #(message_end - message)
    svc #0x3d /* OutputDebugString */
    svc #0x03 /* ExitProcess */
    b .
message:
    .ascii "[3dsnative smoke] guest ARM11 executed\n"
message_end:
.balign 4
