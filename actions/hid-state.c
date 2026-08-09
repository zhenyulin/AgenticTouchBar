// Live keyboard-modifier and mouse-button state, for the freeze guard's
// pre-restart safety check.
//
// The guard used to gate on IOHIDSystem's HIDIdleTime, which is the time since
// the last HID *event*. That cannot see a key that is merely being held:
// modifier keys emit a single flagsChanged on press and never auto-repeat, and
// a drag paused with the button down posts nothing at all. Both read as "idle"
// within a second -- and both are exactly the states that leave the front app
// with a latched Shift or a phantom mouse-down when BTT's event tap dies
// mid-gesture.
//
// CGEventSourceFlagsState/ButtonState ask the window server for the current
// state instead of inferring it from event timing. Neither is TCC-gated (unlike
// installing an event tap), so this needs no accessibility grant.
//
// Build (the guard runs from ~/Library/Application Support/BTT, so the binary
// lives beside the deployed script -- launchd's zsh cannot read ~/Documents):
//
//   clang -O2 actions/hid-state.c -framework ApplicationServices \
//       -o ~/Library/Application\ Support/BTT/hid-state
//
// Prints "held ..." or "clear ..." and exits 1 when something is held, so
// callers can branch on either the status or the text.

#include <ApplicationServices/ApplicationServices.h>
#include <stdio.h>

// Caps lock is a latch, not a held key, and the non-coalesced bit is not a
// modifier at all -- neither should block a restart.
static const CGEventFlags kHeldModifierMask =
    kCGEventFlagMaskShift | kCGEventFlagMaskControl | kCGEventFlagMaskAlternate |
    kCGEventFlagMaskCommand | kCGEventFlagMaskSecondaryFn;

int main(void) {
    CGEventFlags flags =
        CGEventSourceFlagsState(kCGEventSourceStateCombinedSessionState);

    // Buttons 0-4: left, right, middle, and the two side buttons.
    int buttons = 0;
    for (CGMouseButton button = 0; button <= 4; button++) {
        if (CGEventSourceButtonState(kCGEventSourceStateCombinedSessionState,
                                     button)) {
            buttons |= 1 << button;
        }
    }

    int held = (flags & kHeldModifierMask) != 0 || buttons != 0;
    printf("%s flags=0x%llx buttons=0x%x\n", held ? "held" : "clear",
           (unsigned long long)(flags & kHeldModifierMask), buttons);
    return held ? 1 : 0;
}
