// UIKit owns the current scene/window geometry, including Duo display handoff.
#import <UIKit/UIKit.h>
#import <AVFAudio/AVFAudio.h>
#include "runtime.h"
UIViewController *front_ios_root(void);
BOOL front_ios_input(UIViewController *, NSURL *);
void n3ds_audio_session(void) {
    AVAudioSession *session = AVAudioSession.sharedInstance;
    NSError *error;
    if (![session setCategory:AVAudioSessionCategoryPlayback error:&error] || ![session setActive:YES error:&error])
        NSLog(@"[RED] Audio session activation failed: %@", error.localizedDescription);
    else NSLog(@"3dsnative audio session: Playback, %.0f Hz", session.sampleRate);
}
@interface N3DSSceneDelegate : UIResponder <UIWindowSceneDelegate>
@property(nonatomic, strong) UIWindow *window;
@end
@implementation N3DSSceneDelegate
- (void)scene:(UIScene *)scene willConnectToSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options {
    self.window = [[UIWindow alloc] initWithWindowScene:(UIWindowScene *)scene];
    // Making the root visible can start the boot thread immediately. Establish
    // Playback first so RemoteIO never races audio-session activation.
    n3ds_audio_session();
    self.window.rootViewController = front_ios_root();
    [self.window makeKeyAndVisible];
    UIApplication.sharedApplication.idleTimerDisabled = YES;
}
- (void)scene:(UIScene *)scene openURLContexts:(NSSet<UIOpenURLContext *> *)contexts {
    for (UIOpenURLContext *context in contexts) front_ios_input(self.window.rootViewController, context.URL);
}
@end
@interface N3DSAppDelegate : UIResponder <UIApplicationDelegate>
@end
@implementation N3DSAppDelegate
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options { return YES; }
- (void)applicationWillTerminate:(UIApplication *)application { n3ds_stop(); }
@end
int main(int argc, char **argv) {
    @autoreleasepool { return UIApplicationMain(argc, argv, nil, NSStringFromClass(N3DSAppDelegate.class)); }
}
