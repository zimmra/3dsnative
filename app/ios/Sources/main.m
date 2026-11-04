// iOS-only entry point. The runtime owns Documents/log.txt; the shell owns UIKit.
#import <UIKit/UIKit.h>
#include "runtime.h"

UIViewController *front_ios_root(void);
BOOL front_ios_input(UIViewController *, NSURL *);

@interface N3DSAppDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic, strong) UIWindow *window;
@end
@implementation N3DSAppDelegate
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)options {
    self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
    self.window.rootViewController = front_ios_root();
    [self.window makeKeyAndVisible];
    return YES;
}
- (BOOL)application:(UIApplication *)application openURL:(NSURL *)url options:(NSDictionary *)options {
    return front_ios_input(self.window.rootViewController, url);
}
- (void)applicationWillTerminate:(UIApplication *)application { n3ds_stop(); }
@end

int main(int argc, char **argv) {
    @autoreleasepool { return UIApplicationMain(argc, argv, nil, NSStringFromClass(N3DSAppDelegate.class)); }
}
