//! Discord OAuth2: token exchange, refresh, credential check and the browser fallback flow.

use async_trait::async_trait;
use serde_json::{json, Value};
use std::rc::Rc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::watch;

use crate::i18n::tr;

pub const API: &str = "https://discord.com/api/v10";
pub const SCOPES: [&str; 4] = ["rpc", "rpc.voice.read", "rpc.voice.write", "identify"];
const HTTP_TIMEOUT: Duration = Duration::from_secs(15);
/// Refresh tokens that expire within 5 minutes.
const REFRESH_MARGIN: f64 = 300.0;

#[derive(Clone, Debug, PartialEq)]
pub enum AuthError {
    /// The user has to authorize the application (again).
    Refused(String),
    /// Network or Discord-side failure: retry later, do not prompt the user.
    Transient(String),
}

impl std::fmt::Display for AuthError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AuthError::Refused(m) | AuthError::Transient(m) => f.write_str(m),
        }
    }
}

pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

pub fn token_is_fresh(token: Option<&Value>, now: f64) -> bool {
    let Some(token) = token else { return false };
    if token["access_token"].as_str().is_none_or(str::is_empty) {
        return false;
    }
    // Tokens saved by version 1.x have no expiry: refresh them.
    token["expires_at"]
        .as_f64()
        .is_some_and(|exp| exp - REFRESH_MARGIN > now)
}

/// Where tokens are kept (the app uses the system credential store).
pub trait TokenStore {
    fn get_token(&self) -> Option<Value>;
    fn set_token(&self, token: Option<Value>) -> std::io::Result<()>;
}

/// What the engine needs for authorization (faked in tests).
#[async_trait(?Send)]
pub trait TokenService {
    /// A usable access token, None when the user has to authorize, Err(Transient) when offline.
    async fn access_token(
        &self,
        client_id: &str,
        secret: &str,
        force_refresh: bool,
    ) -> Result<Option<String>, AuthError>;
    /// Exchange an authorization code, trying each redirect URI in turn.
    async fn exchange(
        &self,
        client_id: &str,
        secret: &str,
        code: &str,
        redirect_uris: &[Option<String>],
    ) -> Result<String, AuthError>;
    fn clear(&self);
    /// Browser fallback: returns the authorization code.
    async fn browser_authorize(
        &self,
        client_id: &str,
        redirect_uri: &str,
        port: u16,
        timeout: Duration,
        cancel: watch::Receiver<bool>,
    ) -> Result<String, AuthError>;
}

pub struct DiscordTokens {
    pub store: Rc<dyn TokenStore>,
    pub api: String,
    http: reqwest::Client,
    /// Opens a URL in the user's browser.
    pub open_url: Rc<dyn Fn(&str)>,
}

impl DiscordTokens {
    pub fn new(store: Rc<dyn TokenStore>, open_url: Rc<dyn Fn(&str)>) -> Self {
        Self::with_api(store, open_url, API)
    }

    pub fn with_api(store: Rc<dyn TokenStore>, open_url: Rc<dyn Fn(&str)>, api: &str) -> Self {
        let http = reqwest::Client::builder()
            .timeout(HTTP_TIMEOUT)
            .build()
            .expect("HTTP client");
        DiscordTokens {
            store,
            api: api.to_string(),
            http,
            open_url,
        }
    }

    async fn token_request(&self, form: &[(&str, &str)]) -> Result<Value, AuthError> {
        let response = self
            .http
            .post(format!("{}/oauth2/token", self.api))
            .form(form)
            .send()
            .await
            .map_err(|e| AuthError::Transient(format!("Cannot reach Discord: {e}")))?;
        let status = response.status().as_u16();
        if status == 429 || status >= 500 {
            return Err(AuthError::Transient(format!(
                "Discord answered HTTP {status}"
            )));
        }
        let mut body: Value = response.json().await.unwrap_or(Value::Null);
        if status != 200 || body["access_token"].as_str().is_none() {
            let reason = body["error_description"]
                .as_str()
                .or(body["error"].as_str())
                .map(str::to_string)
                .unwrap_or_else(|| format!("HTTP {status}"));
            return Err(AuthError::Refused(format!(
                "Discord refused the token request: {reason}"
            )));
        }
        body["expires_at"] = json!(now() + body["expires_in"].as_f64().unwrap_or(0.0));
        Ok(body)
    }

    pub async fn refresh(
        &self,
        client_id: &str,
        secret: &str,
        refresh: &str,
    ) -> Result<Value, AuthError> {
        self.token_request(&[
            ("client_id", client_id),
            ("client_secret", secret),
            ("grant_type", "refresh_token"),
            ("refresh_token", refresh),
        ])
        .await
    }

    pub async fn exchange_code(
        &self,
        client_id: &str,
        secret: &str,
        code: &str,
        redirect_uri: Option<&str>,
    ) -> Result<Value, AuthError> {
        let mut form = vec![
            ("client_id", client_id),
            ("client_secret", secret),
            ("grant_type", "authorization_code"),
            ("code", code),
        ];
        if let Some(uri) = redirect_uri {
            form.push(("redirect_uri", uri));
        }
        self.token_request(&form).await
    }

    pub async fn check_credentials(&self, client_id: &str, secret: &str) -> (bool, String) {
        check_credentials_at(&self.api, client_id, secret).await
    }
}

/// Validate an application ID/secret pair without any user interaction (Send).
pub async fn check_credentials_at(api: &str, client_id: &str, secret: &str) -> (bool, String) {
    let http = reqwest::Client::builder()
        .timeout(HTTP_TIMEOUT)
        .build()
        .expect("HTTP client");
    let response = http
        .post(format!("{api}/oauth2/token"))
        .basic_auth(client_id, Some(secret))
        .form(&[("grant_type", "client_credentials"), ("scope", "identify")])
        .send()
        .await;
    match response {
        Err(e) => (
            false,
            tr(
                "Cannot reach Discord ({error}). Check your internet connection.",
                &[("error", &short_error(&e))],
            ),
        ),
        Ok(r) if r.status().as_u16() == 200 => {
            (true, tr("The Client ID and Client Secret are valid.", &[]))
        }
        Ok(r) if r.status().as_u16() == 401 => (
            false,
            tr(
                "Discord rejected this Client ID / Client Secret pair. Copy both values again.",
                &[],
            ),
        ),
        Ok(r) => (
            false,
            tr(
                "Discord answered HTTP {status}. Try again in a moment.",
                &[("status", &r.status().as_u16().to_string())],
            ),
        ),
    }
}

fn short_error(e: &reqwest::Error) -> String {
    if e.is_timeout() {
        "timeout".into()
    } else if e.is_connect() {
        "connection".into()
    } else {
        "request".into()
    }
}

#[async_trait(?Send)]
impl TokenService for DiscordTokens {
    async fn access_token(
        &self,
        client_id: &str,
        secret: &str,
        force_refresh: bool,
    ) -> Result<Option<String>, AuthError> {
        let token = self.store.get_token();
        if !force_refresh && token_is_fresh(token.as_ref(), now()) {
            return Ok(token.and_then(|t| t["access_token"].as_str().map(str::to_string)));
        }
        let Some(refresh) = token
            .as_ref()
            .and_then(|t| t["refresh_token"].as_str())
            .filter(|r| !r.is_empty())
        else {
            return Ok(None);
        };
        match self.refresh(client_id, secret, refresh).await {
            Ok(new_token) => {
                let access = new_token["access_token"].as_str().map(str::to_string);
                self.store
                    .set_token(Some(new_token))
                    .map_err(|e| AuthError::Transient(e.to_string()))?;
                log::info!("Discord token refreshed");
                Ok(access)
            }
            Err(AuthError::Refused(reason)) => {
                log::warn!("Token refresh refused: {reason}");
                self.clear();
                Ok(None)
            }
            Err(e) => Err(e),
        }
    }

    async fn exchange(
        &self,
        client_id: &str,
        secret: &str,
        code: &str,
        redirect_uris: &[Option<String>],
    ) -> Result<String, AuthError> {
        let mut last = AuthError::Refused("No redirect URI to try".into());
        for uri in redirect_uris {
            match self
                .exchange_code(client_id, secret, code, uri.as_deref())
                .await
            {
                Ok(token) => {
                    let access = token["access_token"]
                        .as_str()
                        .unwrap_or_default()
                        .to_string();
                    self.store
                        .set_token(Some(token))
                        .map_err(|e| AuthError::Transient(e.to_string()))?;
                    return Ok(access);
                }
                Err(e @ AuthError::Transient(_)) => return Err(e),
                Err(e) => last = e,
            }
        }
        Err(last)
    }

    fn clear(&self) {
        if let Err(e) = self.store.set_token(None) {
            log::warn!("Cannot clear the Discord token: {e}");
        }
    }

    async fn browser_authorize(
        &self,
        client_id: &str,
        redirect_uri: &str,
        port: u16,
        timeout: Duration,
        cancel: watch::Receiver<bool>,
    ) -> Result<String, AuthError> {
        browser_authorize(
            client_id,
            redirect_uri,
            port,
            timeout,
            cancel,
            &*self.open_url,
        )
        .await
    }
}

fn random_state() -> String {
    let mut bytes = [0u8; 24];
    getrandom::fill(&mut bytes).expect("system random generator");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn page(status: &str, title: &str, text: &str) -> Vec<u8> {
    let body = format!("<!doctype html><meta charset='utf-8'><title>GoXLR Discord Sync</title>\
        <body style='font-family:system-ui,sans-serif;text-align:center;padding-top:60px'><h1>{title}</h1><p>{text}</p></body>");
    format!("HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()).into_bytes()
}

/// Browser OAuth flow with a one-shot local callback server. Returns the code.
pub async fn browser_authorize(
    client_id: &str,
    redirect_uri: &str,
    port: u16,
    timeout: Duration,
    mut cancel: watch::Receiver<bool>,
    open_url: &dyn Fn(&str),
) -> Result<String, AuthError> {
    let listener = TcpListener::bind(("127.0.0.1", port)).await.map_err(|_| {
        AuthError::Refused(format!(
            "Port {port} is already in use, cannot receive the Discord callback"
        ))
    })?;
    let state = random_state();
    let mut url = url::Url::parse("https://discord.com/oauth2/authorize").unwrap();
    url.query_pairs_mut()
        .append_pair("client_id", client_id)
        .append_pair("redirect_uri", redirect_uri)
        .append_pair("response_type", "code")
        .append_pair("scope", &SCOPES.join(" "))
        .append_pair("state", &state)
        .append_pair("prompt", "consent");
    log::info!("Opening the browser for Discord authorization");
    open_url(url.as_str());
    let deadline = Instant::now() + timeout;
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        let accepted = tokio::select! {
            r = listener.accept() => r,
            _ = tokio::time::sleep(remaining) => {
                return Err(AuthError::Refused(format!("No answer from the browser within {} seconds", timeout.as_secs())));
            }
            _ = cancel.wait_for(|c| *c) => return Err(AuthError::Refused("Authorization cancelled".into())),
        };
        let Ok((mut socket, _)) = accepted else {
            continue;
        };
        let mut buf = vec![0u8; 8192];
        // An idle browser preconnect must not block the wait.
        let n = match tokio::time::timeout(Duration::from_secs(5), socket.read(&mut buf)).await {
            Ok(Ok(n)) => n,
            _ => continue,
        };
        let request = String::from_utf8_lossy(&buf[..n]);
        let target = request.split_whitespace().nth(1).unwrap_or("/");
        let Ok(parsed) = url::Url::parse(&format!("http://127.0.0.1{target}")) else {
            continue;
        };
        if parsed.path() != "/callback" {
            let _ = socket
                .write_all(&page("404 Not Found", "Not found", ""))
                .await;
            continue;
        }
        let param = |name: &str| {
            parsed
                .query_pairs()
                .find(|(k, _)| k == name)
                .map(|(_, v)| v.into_owned())
        };
        if param("state").as_deref() != Some(state.as_str()) {
            let _ = socket
                .write_all(&page(
                    "400 Bad Request",
                    "Invalid request",
                    "This authorization link is not the one GoXLR Discord Sync opened.",
                ))
                .await;
            continue;
        }
        if let Some(error) = param("error") {
            let _ = socket
                .write_all(&page(
                    "200 OK",
                    "Authorization cancelled",
                    "You can close this window.",
                ))
                .await;
            let reason = param("error_description").unwrap_or(error);
            return Err(AuthError::Refused(format!(
                "Authorization denied: {reason}"
            )));
        }
        if let Some(code) = param("code") {
            let _ = socket
                .write_all(&page(
                    "200 OK",
                    "Authorization successful",
                    "You can close this window and return to GoXLR Discord Sync.",
                ))
                .await;
            return Ok(code);
        }
        let _ = socket
            .write_all(&page(
                "400 Bad Request",
                "Invalid request",
                "No authorization code received.",
            ))
            .await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    #[derive(Default)]
    struct MemoryStore(RefCell<Option<Value>>);
    impl TokenStore for MemoryStore {
        fn get_token(&self) -> Option<Value> {
            self.0.borrow().clone()
        }
        fn set_token(&self, token: Option<Value>) -> std::io::Result<()> {
            *self.0.borrow_mut() = token;
            Ok(())
        }
    }

    /// Minimal fake of Discord's token endpoint: answers each request with the next canned reply.
    async fn fake_api(replies: Vec<(u16, Value)>) -> (String, Rc<RefCell<Vec<String>>>) {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let api = format!("http://{}", listener.local_addr().unwrap());
        let seen: Rc<RefCell<Vec<String>>> = Rc::default();
        let log = seen.clone();
        tokio::task::spawn_local(async move {
            for (status, body) in replies {
                let (mut s, _) = listener.accept().await.unwrap();
                let mut buf = vec![0u8; 16384];
                let n = s.read(&mut buf).await.unwrap();
                log.borrow_mut()
                    .push(String::from_utf8_lossy(&buf[..n]).to_string());
                let text = body.to_string();
                let reply = format!("HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{text}", text.len());
                s.write_all(reply.as_bytes()).await.unwrap();
            }
        });
        (api, seen)
    }

    fn tokens(store: Rc<MemoryStore>, api: &str) -> DiscordTokens {
        DiscordTokens::with_api(store, Rc::new(|_: &str| {}), api)
    }

    async fn local<F: std::future::Future>(f: F) -> F::Output {
        tokio::task::LocalSet::new().run_until(f).await
    }

    #[test]
    fn freshness() {
        let now = 1000.0;
        assert!(token_is_fresh(
            Some(&json!({"access_token": "a", "expires_at": now + 3600.0})),
            now
        ));
        assert!(!token_is_fresh(
            Some(&json!({"access_token": "a", "expires_at": now + 60.0})),
            now
        ));
        assert!(!token_is_fresh(Some(&json!({"access_token": "a"})), now)); // version 1.x
        assert!(!token_is_fresh(None, now));
    }

    #[tokio::test(flavor = "current_thread")]
    async fn fresh_token_is_reused_without_http() {
        local(async {
            let store = Rc::new(MemoryStore(RefCell::new(Some(
                json!({"access_token": "a", "refresh_token": "r",
                "expires_at": now() + 3600.0}),
            ))));
            let t = tokens(store, &closed_api()); // any HTTP call would fail
            assert_eq!(
                t.access_token("id", "s", false).await.unwrap().as_deref(),
                Some("a")
            );
        })
        .await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn expired_token_is_refreshed_and_saved() {
        local(async {
            let (api, seen) = fake_api(vec![(
                200,
                json!({"access_token": "b", "refresh_token": "r2", "expires_in": 604800}),
            )])
            .await;
            let store = Rc::new(MemoryStore(RefCell::new(Some(
                json!({"access_token": "a", "refresh_token": "r", "expires_at": 0}),
            ))));
            let t = tokens(store.clone(), &api);
            assert_eq!(
                t.access_token("id", "s", false).await.unwrap().as_deref(),
                Some("b")
            );
            let saved = store.get_token().unwrap();
            assert_eq!(saved["refresh_token"], "r2");
            assert!(saved["expires_at"].as_f64().unwrap() > now() + 600000.0);
            assert!(seen.borrow()[0].contains("grant_type=refresh_token"));
        })
        .await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn refused_refresh_clears_and_server_error_keeps() {
        local(async {
            let (api, _) = fake_api(vec![
                (400, json!({"error": "invalid_grant"})),
                (500, json!({})),
            ])
            .await;
            let store = Rc::new(MemoryStore(RefCell::new(Some(
                json!({"access_token": "a", "refresh_token": "r", "expires_at": 0}),
            ))));
            let t = tokens(store.clone(), &api);
            assert_eq!(t.access_token("id", "s", false).await.unwrap(), None);
            assert!(store.get_token().is_none());
            store
                .set_token(Some(
                    json!({"access_token": "a", "refresh_token": "r", "expires_at": 0}),
                ))
                .unwrap();
            assert!(matches!(
                t.access_token("id", "s", false).await,
                Err(AuthError::Transient(_))
            ));
            assert!(store.get_token().is_some());
            let offline = tokens(store.clone(), &closed_api());
            assert!(matches!(
                offline.access_token("id", "s", false).await,
                Err(AuthError::Transient(_))
            ));
        })
        .await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn exchange_tries_redirect_uris_in_order() {
        local(async {
            let (api, seen) = fake_api(vec![
                (
                    400,
                    json!({"error": "invalid_grant", "error_description": "redirect_uri mismatch"}),
                ),
                (200, json!({"access_token": "t", "expires_in": 10})),
            ])
            .await;
            let store = Rc::new(MemoryStore::default());
            let t = tokens(store.clone(), &api);
            let got = t
                .exchange("id", "s", "c", &[Some("http://x".into()), None])
                .await
                .unwrap();
            assert_eq!(got, "t");
            assert!(seen.borrow()[0].contains("redirect_uri=http"));
            assert!(!seen.borrow()[1].contains("redirect_uri"));
            assert_eq!(store.get_token().unwrap()["access_token"], "t");
        })
        .await;
    }

    async fn simulate_browser(
        port_holder: Rc<RefCell<u16>>,
        queries: Vec<&'static str>,
    ) -> impl Fn(&str) {
        move |url: &str| {
            let state = url::Url::parse(url)
                .unwrap()
                .query_pairs()
                .find(|(k, _)| k == "state")
                .unwrap()
                .1
                .into_owned();
            let port = *port_holder.borrow();
            let queries: Vec<String> = queries.iter().map(|q| q.replace("STATE", &state)).collect();
            tokio::task::spawn_local(async move {
                for q in queries {
                    let _ = reqwest::get(format!("http://127.0.0.1:{port}/callback?{q}")).await;
                }
            });
        }
    }

    /// A port that was just released: connecting to it is refused at once.
    fn closed_api() -> String {
        format!("http://127.0.0.1:{}", free_port())
    }

    fn free_port() -> u16 {
        std::net::TcpListener::bind("127.0.0.1:0")
            .unwrap()
            .local_addr()
            .unwrap()
            .port()
    }

    #[tokio::test(flavor = "current_thread")]
    async fn browser_flow_checks_state_and_reports_denial() {
        local(async {
            let port = free_port();
            let (_tx, rx) = watch::channel(false);
            let open = simulate_browser(
                Rc::new(RefCell::new(port)),
                vec!["code=forged&state=wrong", "code=good&state=STATE"],
            )
            .await;
            let code = browser_authorize(
                "id",
                "http://x",
                port,
                Duration::from_secs(5),
                rx.clone(),
                &open,
            )
            .await;
            assert_eq!(code.unwrap(), "good");

            let port = free_port();
            let open = simulate_browser(
                Rc::new(RefCell::new(port)),
                vec!["error=access_denied&state=STATE"],
            )
            .await;
            let denied =
                browser_authorize("id", "http://x", port, Duration::from_secs(5), rx, &open).await;
            assert!(matches!(denied, Err(AuthError::Refused(m)) if m.contains("denied")));
        })
        .await;
    }

    #[tokio::test(flavor = "current_thread")]
    async fn browser_flow_timeout_cancel_and_busy_port() {
        local(async {
            let (tx, rx) = watch::channel(false);
            let none = |_: &str| {};
            let timeout = browser_authorize(
                "id",
                "http://x",
                free_port(),
                Duration::from_millis(300),
                rx.clone(),
                &none,
            )
            .await;
            assert!(matches!(timeout, Err(AuthError::Refused(m)) if m.contains("No answer")));
            tx.send(true).unwrap();
            let cancelled = browser_authorize(
                "id",
                "http://x",
                free_port(),
                Duration::from_secs(5),
                rx.clone(),
                &none,
            )
            .await;
            assert!(matches!(cancelled, Err(AuthError::Refused(m)) if m.contains("cancelled")));
            let busy = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            let port = busy.local_addr().unwrap().port();
            let refused =
                browser_authorize("id", "http://x", port, Duration::from_secs(1), rx, &none).await;
            assert!(matches!(refused, Err(AuthError::Refused(m)) if m.contains("already in use")));
        })
        .await;
    }
}
