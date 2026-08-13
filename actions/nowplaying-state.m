// Reports the system Now Playing app's true playback state and its info
// dictionary, in one call.
// cspell:ignore NSEC NSJSON SIGSEGV fputc fwrite nowplaying
//
// nowplaying-cli's raw dictionary cannot tell pause from play for QQ Music:
// the app pushes a playbackRate of 1 even while paused (it only dips to 0
// for a moment at the pause event itself), and it freezes the elapsed-time
// field between player events, so a frozen elapsed time reads the same
// paused or playing. MRMediaRemoteGetNowPlayingApplicationIsPlaying is the
// same source Control Center's Now Playing tile reads, and it reports the
// truth -- 1 while playing, 0 while paused -- so the lyrics widget can hold
// its frame instead of scrolling through a paused track.
//
// Build (the lyrics sampler runs from the user session, but the binary lives
// beside hid-state in BTT's support directory, keeping the repo free of
// compiled artifacts):
//
//   clang -O2 actions/nowplaying-state.m -framework Foundation \
//       -F/System/Library/PrivateFrameworks -framework MediaRemote \
//       -o ~/Library/Application\ Support/BTT/nowplaying-state
//
// Prints one JSON object: an "isPlaying" boolean plus the Now Playing info
// dictionary under its kMRMediaRemoteNowPlayingInfo* keys. Reads as
// {"isPlaying": false} when no app is playing (or MediaRemote is silent,
// after an internal timeout).
//
// NSData values need care, because two of them are the whole reason a caller
// asks MediaRemote anything:
//
//   ...ArtworkData           the album cover. Emitted as base64, the same
//                            shape nowplaying-cli get-raw produces, so a
//                            caller that has this helper does not also need
//                            nowplaying-cli (~0.22 s) for the cover.
//   ...ClientPropertiesData  a serialized protobuf describing the session
//                            holder. The holder's bundle id is in there and
//                            nowhere else in this dictionary -- the widgets'
//                            allowlists are built on it. Decoded through
//                            MediaRemote's own private protobuf class when
//                            that is available, and published under the
//                            ...ClientBundleIdentifier key nowplaying-cli
//                            synthesizes, so both sources read alike.
//
// Every other NSData is still dropped: nothing reads them, and base64 of an
// unknown blob is only cost.

#import <Foundation/Foundation.h>
#import <objc/runtime.h>

// Private MediaRemote framework; declared here so no headers are needed.
extern void MRMediaRemoteGetNowPlayingInfo(
    dispatch_queue_t queue, void (^completion)(NSDictionary *info));
extern void MRMediaRemoteGetNowPlayingApplicationIsPlaying(
    dispatch_queue_t queue, void (^completion)(BOOL isPlaying));

static NSString *const kArtworkKey = @"kMRMediaRemoteNowPlayingInfoArtworkData";
static NSString *const kClientPropertiesKey =
    @"kMRMediaRemoteNowPlayingInfoClientPropertiesData";
static NSString *const kBundleIdentifierKey =
    @"kMRMediaRemoteNowPlayingInfoClientBundleIdentifier";

// The session holder's bundle id, out of the client-properties protobuf.
//
// _MRNowPlayingClientProtobuf is private and undeclared, so every step is
// checked before it is taken and the whole thing is wrapped: a helper that
// crashes here would take the play/pause state down with it, and the callers
// have another way to learn the bundle id (nowplaying-cli). Returns nil
// whenever the class, the initializer or the property is not what we expect.
static NSString *BundleIdentifierFromClientProperties(NSData *properties) {
    if (properties.length == 0) {
        return nil;
    }
    Class protobuf = objc_getClass("_MRNowPlayingClientProtobuf");
    if (protobuf == nil ||
        ![protobuf instancesRespondToSelector:@selector(initWithData:)]) {
        return nil;
    }
    @try {
        id client = [[protobuf alloc] performSelector:@selector(initWithData:)
                                           withObject:properties];
        if (client == nil) {
            return nil;
        }
        id identifier = [client valueForKey:@"bundleIdentifier"];
        if ([identifier isKindOfClass:[NSString class]] &&
            [(NSString *)identifier length] > 0) {
            return identifier;
        }
    } @catch (NSException *exception) {
        // Private API drifted; the caller falls back to nowplaying-cli.
    }
    return nil;
}

static NSDictionary *JSONSafeInfo(NSDictionary *info) {
    NSMutableDictionary *safe = [NSMutableDictionary dictionary];
    for (NSString *key in info) {
        id value = info[key];
        if ([value isKindOfClass:[NSData class]]) {
            NSData *data = (NSData *)value;
            if ([key isEqualToString:kArtworkKey]) {
                safe[key] = [data base64EncodedStringWithOptions:0];
            } else if ([key isEqualToString:kClientPropertiesKey]) {
                NSString *bundleIdentifier =
                    BundleIdentifierFromClientProperties(data);
                if (bundleIdentifier != nil) {
                    safe[kBundleIdentifierKey] = bundleIdentifier;
                }
            }
            continue;
        }
        if ([value isKindOfClass:[NSDate class]]) {
            safe[key] = @([(NSDate *)value timeIntervalSince1970]);
            continue;
        }
        // Scalar values (strings, numbers, NSNull) are always JSON-safe.
        // isValidJSONObject: cannot vouch for them: it only accepts the
        // top-level shapes JSONSerialization writes, arrays and dictionaries.
        if ([value isKindOfClass:[NSString class]] ||
            [value isKindOfClass:[NSNumber class]] ||
            value == [NSNull null] ||
            [NSJSONSerialization isValidJSONObject:value]) {
            safe[key] = value;
        }
    }
    return safe;
}

// MediaRemote is an unannotated C API: it receives these completions as raw
// block pointers and invokes them later on its own queue, so each literal is
// copied to the heap before it is handed over -- a stack literal's lifetime
// ends with main's frame, and under -O2 the framework's late invocation of a
// freed block was a SIGSEGV. Each query gets a fresh semaphore, and the two
// queries run one after the other: that is the combination verified stable
// under repeated invocation, so the sampler's once-a-second run is safe.
//
// The info block must also take ownership of what it is handed. This file is
// compiled without ARC, and MediaRemote passes an autoreleased dictionary
// that it drains on its own queue as soon as the block returns -- so simply
// assigning it left main messaging freed memory once the semaphore came back.
// Measured on 2026-08-13: a binary built from the assigning version crashed
// 18 runs out of 20, while the copy below is clean. Anything read out of the
// completion has to be copied or retained here, not just pointed at.

int main(void) {
    dispatch_queue_t queue =
        dispatch_get_global_queue(DISPATCH_QUEUE_PRIORITY_DEFAULT, 0);
    __block BOOL isPlaying = NO;
    dispatch_semaphore_t sem = dispatch_semaphore_create(0);
    void (^isPlayingBlock)(BOOL) = [^(BOOL playing) {
        isPlaying = playing;
        dispatch_semaphore_signal(sem);
    } copy];
    MRMediaRemoteGetNowPlayingApplicationIsPlaying(queue, isPlayingBlock);
    long waited = dispatch_semaphore_wait(
        sem, dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.2 * NSEC_PER_SEC)));

    // No now-playing app at all: the framework may never answer. With
    // nothing holding a session the info query would hang the same way, so
    // report the empty state instead of paying a second timeout.
    if (waited != 0) {
        NSMutableDictionary *empty = [NSMutableDictionary dictionary];
        empty[@"isPlaying"] = @NO;
        NSData *emptyData =
            [NSJSONSerialization dataWithJSONObject:empty options:0 error:NULL];
        fwrite(emptyData.bytes, 1, emptyData.length, stdout);
        fputc('\n', stdout);
        return 0;
    }

    sem = dispatch_semaphore_create(0);
    __block NSDictionary *info = nil;
    void (^infoBlock)(NSDictionary *) = [^(NSDictionary *dict) {
        info = [dict copy];
        dispatch_semaphore_signal(sem);
    } copy];
    MRMediaRemoteGetNowPlayingInfo(queue, infoBlock);
    dispatch_semaphore_wait(
        sem, dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1.2 * NSEC_PER_SEC)));

    NSMutableDictionary *payload = [NSMutableDictionary dictionary];
    payload[@"isPlaying"] = @(isPlaying);
    if (info != nil) {
        [payload addEntriesFromDictionary:JSONSafeInfo(info)];
    }
    NSData *data =
        [NSJSONSerialization dataWithJSONObject:payload options:0 error:NULL];
    fwrite(data.bytes, 1, data.length, stdout);
    fputc('\n', stdout);
    return 0;
}
