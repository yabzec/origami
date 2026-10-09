#import <Cocoa/Cocoa.h>
#include "_cgo_export.h"

@interface OrigamiURLHandler : NSObject
@end

@implementation OrigamiURLHandler
- (void)handle:(NSAppleEventDescriptor *)event withReply:(NSAppleEventDescriptor *)reply {
    NSString *url = [[event paramDescriptorForKeyword:keyDirectObject] stringValue];
    if (url != nil) {
        goHandleURL((char *)[url UTF8String]);
    }
}
@end

void runApp(void) {
    [NSApplication sharedApplication];
    OrigamiURLHandler *handler = [OrigamiURLHandler new];
    [[NSAppleEventManager sharedAppleEventManager]
        setEventHandler:handler
            andSelector:@selector(handle:withReply:)
          forEventClass:kInternetEventClass
             andEventID:kAEGetURL];
    [NSApp run];
}
