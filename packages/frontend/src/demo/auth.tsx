// The public demo's fixed identity: no Cognito, no tokens. useAuth() anywhere
// in the app sees a signed-in "Asha Verma" (asha.verma, a made-up persona).
import type { PropsWithChildren } from 'react';
import { AuthContext, type AuthContextProps } from 'react-oidc-context';
import { DEMO_DISPLAY_NAME, DEMO_USERNAME } from './mode';

const resolved = async () => undefined;
const events = {
  addUserLoaded: () => () => undefined,
  addUserUnloaded: () => () => undefined,
  addUserSignedIn: () => () => undefined,
  addUserSignedOut: () => () => undefined,
  addAccessTokenExpiring: () => () => undefined,
  addAccessTokenExpired: () => () => undefined,
  addSilentRenewError: () => () => undefined,
  addUserSessionChanged: () => () => undefined,
};

const demoUser = {
  id_token: 'public-demo',
  access_token: 'public-demo',
  token_type: 'Bearer',
  scope: 'openid profile',
  expired: false,
  expires_in: 24 * 3600,
  scopes: ['openid', 'profile'],
  session_state: null,
  state: undefined,
  profile: {
    sub: 'public-demo',
    iss: 'public-demo',
    aud: 'public-demo',
    exp: 0,
    iat: 0,
    'cognito:username': DEMO_USERNAME,
    preferred_username: DEMO_USERNAME,
    name: DEMO_DISPLAY_NAME,
    given_name: 'Asha',
    family_name: 'Verma',
  },
  toStorageString: () => '{}',
};

const demoAuth = {
  user: demoUser,
  isLoading: false,
  isAuthenticated: true,
  activeNavigator: undefined,
  error: undefined,
  settings: {},
  events,
  clearStaleState: resolved,
  removeUser: resolved,
  signinPopup: resolved,
  signinSilent: resolved,
  signinRedirect: resolved,
  signinResourceOwnerCredentials: resolved,
  signoutRedirect: resolved,
  signoutPopup: resolved,
  signoutSilent: resolved,
  querySessionStatus: async () => null,
  revokeTokens: resolved,
  startSilentRenew: () => undefined,
  stopSilentRenew: () => undefined,
} as unknown as AuthContextProps;

export function DemoAuthProvider({ children }: PropsWithChildren) {
  return (
    <AuthContext.Provider value={demoAuth}>{children}</AuthContext.Provider>
  );
}
