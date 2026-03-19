(() => {
  // node_modules/@inboxsdk/core/pageWorld.js
  /*!
   * InboxSDK
   * https://www.inboxsdk.com/
   *
   * The use of InboxSDK is governed by the Terms of Services located at
   * https://www.inboxsdk.com/terms
  
  
   *  __    __            _     _          _                _                      ___                 _ _ ___
   * / / /\ \ \__ _ _ __ | |_  | |_ ___   | |__   __ _  ___| | __   ___  _ __     / _ \_ __ ___   __ _(_) / _ \
   * \ \/  \/ / _` | '_ \| __| | __/ _ \  | '_ \ / _` |/ __| |/ /  / _ \| '_ \   / /_\/ '_ ` _ \ / _` | | \// /
   *  \  /\  / (_| | | | | |_  | || (_) | | | | | (_| | (__|   <  | (_) | | | | / /_\\| | | | | | (_| | | | \/
   *   \/  \/ \__,_|_| |_|\__|  \__\___/  |_| |_|\__,_|\___|_|\_\  \___/|_| |_| \____/|_| |_| |_|\__,_|_|_| ()
   *
   * Like complex reverse engineering? Want to make Gmail and Inbox a hackable platform?
   *
   * Join us at: www.streak.com/careers?source=sdk
   */
  (() => {
    var __webpack_modules__ = {
      8587: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => ajax
        });
        var querystring_es3 = __webpack_require__2(6448);
        var js = __webpack_require__2(8498);
        const r = /([?&])_=[^&]*/;
        let nonce = Date.now() + Math.floor(Math.random() * Math.pow(2, 32));
        function cachebustUrl(url) {
          if (r.test(url)) {
            return url.replace(r, "$1_=" + nonce++);
          } else {
            return url + (/\?/.test(url) ? "&" : "?") + "_=" + nonce++;
          }
        }
        const MAX_TIMEOUT = 64 * 1000;
        const MAX_RETRIES = 5;
        const serversToIgnore = {};
        function ajax(opts) {
          if (!opts || typeof opts.url !== "string") {
            throw new Error("URL must be given");
          }
          return new Promise(function(resolve, reject) {
            const method = opts.method ? opts.method : "GET";
            let url = opts.url;
            let stringData = null;
            if (opts.data) {
              stringData = typeof opts.data === "string" ? opts.data : querystring_es3.stringify(opts.data);
              if (method === "GET" || method === "HEAD") {
                url += (/\?/.test(url) ? "&" : "?") + stringData;
                stringData = null;
              }
            }
            const canRetry = opts.canRetry != null ? opts.canRetry : method === "GET" || method === "HEAD";
            const match = url.match(/(?:(?:[a-z]+:)?\/\/)?([^/]*)\//);
            if (!match) {
              throw new Error("Failed to match url");
            }
            const server = match[1];
            if (Object.prototype.hasOwnProperty.call(serversToIgnore, server)) {
              reject(new Error(`Server at ${url} has told us to stop connecting`));
              return;
            }
            if (opts.cachebust) {
              url = cachebustUrl(url);
            }
            const XMLHttpRequest = opts.XMLHttpRequest || window.XMLHttpRequest;
            const xhr = new XMLHttpRequest;
            Object.assign(xhr, opts.xhrFields);
            xhr.onerror = function(event) {
              if ((opts.retryNum || 0) < MAX_RETRIES) {
                if (xhr.status === 502 || (xhr.status === 0 || xhr.status >= 500) && canRetry) {
                  resolve(_retry(opts));
                  return;
                }
              }
              const err = Object.assign(new Error(`Failed to load ${url}`), {
                event,
                xhr,
                status: xhr.status
              });
              if (xhr.status == 490) {
                serversToIgnore[server] = true;
              }
              reject(err);
            };
            xhr.onload = function(event) {
              if (xhr.status === 200) {
                resolve({
                  xhr,
                  text: xhr.responseText
                });
              } else {
                xhr.onerror(event);
              }
            };
            xhr.open(method, url, true);
            if (opts.headers) {
              const {
                headers
              } = opts;
              Object.keys(headers).forEach((name) => {
                const value = headers[name];
                xhr.setRequestHeader(name, value);
              });
            }
            xhr.send(stringData);
          });
        }
        function _retry(opts) {
          const retryNum = (opts.retryNum || 0) + 1;
          const retryTimeout = Math.min(Math.pow(2, retryNum) * 1000, MAX_TIMEOUT);
          return (0, js.A)(retryTimeout).then(() => ajax(Object.assign({}, opts, {
            retryNum
          })));
        }
      },
      1602: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          v: () => assert
        });

        class AssertionError extends Error {
          name = "AssertionError";
          constructor(message) {
            super(message ?? "assertion failed");
          }
        }
        function assert(condition, message) {
          if (!!condition) {} else {
            throw new AssertionError(message);
          }
        }
      },
      6305: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => htmlToText
        });
        function removeHtmlTags(html) {
          return html.replace(/<[^>]*>?/g, "");
        }
        const removeHtmlTagsPolicy = globalThis.trustedTypes?.createPolicy("inboxSdk__removeHtmlTagsPolicy", {
          createHTML(string) {
            return removeHtmlTags(string);
          }
        }) ?? {
          createHTML(string) {
            return removeHtmlTags(string);
          }
        };
        function htmlToText(html) {
          const div = document.createElement("div");
          div.innerHTML = removeHtmlTagsPolicy.createHTML(html);
          return div.textContent;
        }
      },
      8700: (module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => __WEBPACK_DEFAULT_EXPORT__
        });
        var lodash_escape__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__2(3131);
        var lodash_escape__WEBPACK_IMPORTED_MODULE_0___default = /* @__PURE__ */ __webpack_require__2.n(lodash_escape__WEBPACK_IMPORTED_MODULE_0__);
        var auto_html__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__2(1812);
        var auto_html__WEBPACK_IMPORTED_MODULE_3___default = /* @__PURE__ */ __webpack_require__2.n(auto_html__WEBPACK_IMPORTED_MODULE_3__);
        var ud__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__2(7332);
        var _common_html_to_text__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__2(6305);
        var _platform_implementation_js_dom_driver_gmail_gmail_response_processor__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__2(1433);
        module = __webpack_require__2.hmd(module);
        function modifySuggestions(responseText, modifications) {
          const {
            value: parsed,
            options
          } = _platform_implementation_js_dom_driver_gmail_gmail_response_processor__WEBPACK_IMPORTED_MODULE_2__.iu(responseText);
          const query = parsed[0][1];
          for (const modification of modifications) {
            let name, nameHTML;
            if (typeof modification.name === "string") {
              name = modification.name;
              nameHTML = lodash_escape__WEBPACK_IMPORTED_MODULE_0___default()(name);
            } else if (typeof modification.nameHTML === "string") {
              nameHTML = modification.nameHTML;
              name = (0, _common_html_to_text__WEBPACK_IMPORTED_MODULE_1__.A)(nameHTML);
            }
            if (name == null || nameHTML == null) {
              throw new Error("name or nameHTML must be provided");
            }
            let description, descriptionHTML;
            if (typeof modification.description === "string") {
              description = modification.description;
              descriptionHTML = lodash_escape__WEBPACK_IMPORTED_MODULE_0___default()(description);
            } else if (typeof modification.descriptionHTML === "string") {
              descriptionHTML = modification.descriptionHTML;
              description = (0, _common_html_to_text__WEBPACK_IMPORTED_MODULE_1__.A)(descriptionHTML);
            }
            const data = {
              id: modification.id,
              routeName: modification.routeName,
              routeParams: modification.routeParams,
              externalURL: modification.externalURL
            };
            nameHTML += auto_html__WEBPACK_IMPORTED_MODULE_3___default()` <span style="display:none" data-inboxsdk-suggestion="${JSON.stringify(data)}"></span>`;
            if (modification.iconHTML != null) {
              nameHTML = `<div class="inboxsdk__custom_suggestion_iconHTML">${modification.iconHTML}</div>${nameHTML}`;
            }
            const newItem = [
              "aso.sug",
              modification.searchTerm || query,
              nameHTML,
              null,
              [],
              0,
              null,
              "asor inboxsdk__custom_suggestion " + modification.providerId + " " + (modification.iconClass || ""),
              0
            ];
            if (descriptionHTML != null) {
              newItem[3] = ["aso.eme", description, name, descriptionHTML, nameHTML];
            }
            if (modification.iconHTML != null) {
              newItem[6] = ["aso.thn", "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw=="];
              newItem[7] += " inboxsdk__no_bg";
            } else if (modification.iconUrl) {
              newItem[6] = ["aso.thn", modification.iconUrl];
              newItem[7] += " inboxsdk__no_bg";
            } else {
              newItem[7] += " asor_i4";
            }
            if (Array.isArray(parsed[0][3])) {
              parsed[0][3].push(newItem);
            } else {
              parsed[0][3] = [newItem];
            }
          }
          return _platform_implementation_js_dom_driver_gmail_gmail_response_processor__WEBPACK_IMPORTED_MODULE_2__.lK(parsed, options);
        }
        const __WEBPACK_DEFAULT_EXPORT__ = (0, ud__WEBPACK_IMPORTED_MODULE_4__.defn)(module, modifySuggestions);
      },
      5691: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => setupGmailInterceptor
        });
        var clone = __webpack_require__2(63);
        var clone_default = /* @__PURE__ */ __webpack_require__2.n(clone);
        var flatten2 = __webpack_require__2(4176);
        var flatten_default = /* @__PURE__ */ __webpack_require__2.n(flatten2);
        var find = __webpack_require__2(4455);
        var find_default = /* @__PURE__ */ __webpack_require__2.n(find);
        var intersection = __webpack_require__2(4225);
        var intersection_default = /* @__PURE__ */ __webpack_require__2.n(intersection);
        var includes = __webpack_require__2(5193);
        var includes_default = /* @__PURE__ */ __webpack_require__2.n(includes);
        var bignumber = __webpack_require__2(2180);
        var bignumber_default = /* @__PURE__ */ __webpack_require__2.n(bignumber);
        var kefir_esm = __webpack_require__2(7249);
        var injected_logger = __webpack_require__2(4530);
        var has = __webpack_require__2(5930);
        var has_default = /* @__PURE__ */ __webpack_require__2.n(has);
        var noop = __webpack_require__2(1700);
        var noop_default = /* @__PURE__ */ __webpack_require__2.n(noop);
        var each = __webpack_require__2(5757);
        var each_default = /* @__PURE__ */ __webpack_require__2.n(each);
        var filter = __webpack_require__2(9214);
        var filter_default = /* @__PURE__ */ __webpack_require__2.n(filter);
        var once = __webpack_require__2(8921);
        var once_default = /* @__PURE__ */ __webpack_require__2.n(once);
        var assert = __webpack_require__2(1602);
        var events = __webpack_require__2(4785);
        var events_default = /* @__PURE__ */ __webpack_require__2.n(events);
        var querystring_es3 = __webpack_require__2(6448);
        function isNotNil(value) {
          return value != null;
        }
        const WARNING_TIMEOUT = 60 * 1000;
        function XHRProxyFactory(XHR, wrappers, opts) {
          const logError = opts && opts.logError || function(error) {
            setTimeout(function() {
              throw error;
            }, 1);
          };
          function transformEvent(oldTarget, newTarget, event) {
            const newEvent = {};
            Object.keys(event).concat(["bubbles", "cancelBubble", "cancelable", "defaultPrevented", "preventDefault", "stopPropagation", "stopImmediatePropagation", "lengthComputable", "loaded", "total", "type", "currentTarget", "target", "srcElement", "NONE", "CAPTURING_PHASE", "AT_TARGET", "BUBBLING_PHASE", "eventPhase"]).filter((name) => (name in event)).forEach((name) => {
              const value = event[name];
              if (value === oldTarget) {
                newEvent[name] = newTarget;
              } else if (typeof value === "function") {
                newEvent[name] = value.bind(event);
              } else {
                newEvent[name] = value;
              }
            });
            return newEvent;
          }
          function wrapEventListener(oldTarget, newTarget, listener) {
            return function(event) {
              return listener.call(newTarget, transformEvent(oldTarget, newTarget, event));
            };
          }
          function findApplicableWrappers(wrappers2, connection) {
            return filter_default()(wrappers2, function(wrapper) {
              try {
                return wrapper.isRelevantTo(connection);
              } catch (e) {
                logError(e);
              }
            });
          }
          function XHRProxy() {
            this._wrappers = wrappers;
            this._listeners = {};
            this._boundListeners = {};
            this._events = new (events_default());
            this.responseText = "";
            this._openState = false;
            if (XHR.bind && XHR.bind.apply) {
              this._realxhr = new (XHR.bind.apply(XHR, [null].concat(arguments)));
            } else {
              this._realxhr = new XHR;
            }
            const self2 = this;
            const triggerEventListeners = (name, event) => {
              if (this["on" + name]) {
                try {
                  wrapEventListener(this._realxhr, this, this["on" + name]).call(this, event);
                } catch (e) {
                  logError(e, "XMLHttpRequest event listener error");
                }
              }
              each_default()(this._boundListeners[name], (boundListener) => {
                try {
                  boundListener(event);
                } catch (e) {
                  logError(e, "XMLHttpRequest event listener error");
                }
              });
            };
            const runRscListeners = (event) => {
              triggerEventListeners("readystatechange", event);
            };
            this._fakeRscEvent = function() {
              runRscListeners(Object.freeze({
                bubbles: false,
                cancelBubble: false,
                cancelable: false,
                defaultPrevented: false,
                preventDefault: noop_default(),
                stopPropagation: noop_default(),
                stopImmediatePropagation: noop_default(),
                type: "readystatechange",
                currentTarget: this,
                target: this,
                srcElement: this,
                NONE: 0,
                CAPTURING_PHASE: 1,
                AT_TARGET: 2,
                BUBBLING_PHASE: 3,
                eventPhase: 0
              }));
            };
            const deliverFinalRsc = (event) => {
              this.readyState = 4;
              var wasSuccess = this.status == 200;
              var progressEvent = Object.assign({}, transformEvent(this._realxhr, this, event), {
                lengthComputable: false,
                loaded: 0,
                total: 0
              });
              var supportsResponseText = !this._realxhr.responseType || this._realxhr.responseType == "text";
              if (supportsResponseText) {
                each_default()(this._activeWrappers, (wrapper) => {
                  if (wrapper.finalResponseTextLogger) {
                    try {
                      wrapper.finalResponseTextLogger(this._connection, this.responseText);
                    } catch (e) {
                      logError(e);
                    }
                  }
                });
              }
              runRscListeners(event);
              if (wasSuccess) {
                triggerEventListeners("load", progressEvent);
              } else {
                triggerEventListeners("error", progressEvent);
              }
              triggerEventListeners("loadend", progressEvent);
              each_default()(this._activeWrappers, (wrapper) => {
                if (wrapper.afterListeners) {
                  try {
                    wrapper.afterListeners(this._connection);
                  } catch (e) {
                    logError(e);
                  }
                }
              });
            };
            this._realxhr.addEventListener("readystatechange", (event) => {
              if (!this._connection) {
                return;
              }
              if (this._realxhr.readyState >= 2) {
                this._connection.status = this._realxhr.status;
              }
              const supportsResponseText = !this._realxhr.responseType || this._realxhr.responseType == "text";
              if (this._realxhr.readyState == 4) {
                if (supportsResponseText) {
                  Object.defineProperty(this._connection, "originalResponseText", {
                    enumerable: true,
                    writable: false,
                    configurable: false,
                    value: self2._realxhr.responseText
                  });
                  each_default()(this._activeWrappers, (wrapper) => {
                    if (wrapper.originalResponseTextLogger) {
                      try {
                        wrapper.originalResponseTextLogger(this._connection, this._connection.originalResponseText);
                      } catch (e) {
                        logError(e);
                      }
                    }
                  });
                  const finish = once_default()(deliverFinalRsc.bind(null, event));
                  if (this._connection.async) {
                    const startConnection = this._connection;
                    (async () => {
                      let modifiedResponseText = startConnection.originalResponseText;
                      startConnection.modifiedResponseText = modifiedResponseText;
                      for (const responseTextChanger of this._responseTextChangers) {
                        const longRunWarningTimer = setTimeout(() => {
                          console.warn("responseTextChanger is taking too long", responseTextChanger, startConnection);
                        }, WARNING_TIMEOUT);
                        try {
                          modifiedResponseText = await responseTextChanger(startConnection, modifiedResponseText);
                        } finally {
                          clearTimeout(longRunWarningTimer);
                        }
                        if (typeof modifiedResponseText !== "string") {
                          throw new Error("responseTextChanger returned non-string value " + modifiedResponseText);
                        }
                        startConnection.modifiedResponseText = modifiedResponseText;
                        if (startConnection !== this._connection)
                          break;
                      }
                      return modifiedResponseText;
                    })().then((modifiedResponseText) => {
                      if (startConnection === self2._connection) {
                        this.responseText = modifiedResponseText;
                        finish();
                      }
                    }, (err) => {
                      logError(err);
                      if (startConnection === this._connection) {
                        this.responseText = this._realxhr.responseText;
                        finish();
                      }
                    }).catch(logError);
                    return;
                  } else {
                    self2.responseText = self2._realxhr.responseText;
                  }
                } else {
                  self2.responseText = "";
                }
                deliverFinalRsc(event);
              } else {
                if (self2._realxhr.readyState == 1 && self2.readyState == 1) {
                  return;
                } else if (self2._realxhr.readyState >= 3 && supportsResponseText) {
                  if (self2._responseTextChangers.length) {
                    self2.responseText = "";
                  } else {
                    self2.responseText = self2._realxhr.responseText;
                  }
                } else {
                  self2.responseText = "";
                }
                self2.readyState = self2._realxhr.readyState;
                runRscListeners(event);
              }
            }, false);
            ["dispatchEvent", "getAllResponseHeaders", "getResponseHeader", "overrideMimeType", "responseType", "responseXML", "responseURL", "status", "statusText", "timeout", "ontimeout", "onloadstart", "onprogress", "onabort", "upload", "withCredentials"].forEach(function(prop) {
              Object.defineProperty(self2, prop, {
                enumerable: true,
                configurable: false,
                get: function() {
                  if (typeof self2._realxhr[prop] == "function") {
                    return self2._realxhr[prop].bind(self2._realxhr);
                  }
                  return self2._realxhr[prop];
                },
                set: function(v) {
                  if (typeof v == "function") {
                    v = wrapEventListener(this._realxhr, this, v);
                  }
                  self2._realxhr[prop] = v;
                }
              });
            });
            Object.defineProperty(self2, "response", {
              enumerable: true,
              configurable: false,
              get: function() {
                if (!this._realxhr.responseType || this._realxhr.responseType == "text") {
                  return this.responseText;
                } else {
                  return this._realxhr.response;
                }
              }
            });
            self2.readyState = self2._realxhr.readyState;
          }
          XHRProxy.prototype.abort = function() {
            if (this._clientStartedSend && !this._realStartedSend) {
              if (this.readyState != 0 && this._realxhr.readyState == 0) {
                this._realxhr.open(this._connection.method, this._connection.url);
              }
              this._realStartedSend = true;
              this._realxhr.send();
            }
            this._realxhr.abort();
          };
          XHRProxy.prototype.setRequestHeader = function(name, value) {
            var self2 = this;
            if (this.readyState != 1) {
              console.warn("setRequestHeader improperly called at readyState " + this.readyState);
            }
            if (!this._openState) {
              throw new Error("Can only set headers after open and before send");
            }
            this._connection.headers[name] = value;
            if (this._connection.async && this._requestChangers.length) {
              this._events.once("realOpen", function() {
                self2._realxhr.setRequestHeader(name, value);
              });
            } else {
              this._realxhr.setRequestHeader(name, value);
            }
          };
          XHRProxy.prototype.addEventListener = function(name, listener) {
            if (!this._listeners[name]) {
              this._listeners[name] = [];
              this._boundListeners[name] = [];
            }
            if (!includes_default()(this._listeners[name], listener)) {
              var boundListener = wrapEventListener(this._realxhr, this, listener);
              this._listeners[name].push(listener);
              this._boundListeners[name].push(boundListener);
              if (!includes_default()(["readystatechange", "load", "error", "loadend"], name)) {
                this._realxhr.addEventListener(name, boundListener, false);
              }
            }
          };
          XHRProxy.prototype.removeEventListener = function(name, listener) {
            if (!this._listeners[name]) {
              return;
            }
            var i = this._listeners[name].indexOf(listener);
            if (i == -1) {
              return;
            }
            this._listeners[name].splice(i, 1);
            var boundListener = this._boundListeners[name].splice(i, 1)[0];
            if (name != "readystatechange") {
              this._realxhr.removeEventListener(name, boundListener, false);
            }
          };
          XHRProxy.prototype.open = function(method, url, async) {
            if (!(this instanceof XHRProxy)) {
              return XHR.prototype.open.apply(this, arguments);
            }
            var self2 = this;
            this._connection = {
              method,
              url,
              params: (0, querystring_es3.parse)(url.split("?")[1] || ""),
              headers: {},
              async: arguments.length < 3 || !!async
            };
            this._clientStartedSend = false;
            this._realStartedSend = false;
            this._activeWrappers = findApplicableWrappers(this._wrappers, this._connection);
            this._responseTextChangers = this._activeWrappers.map((wrapper) => {
              return wrapper.responseTextChanger && wrapper.responseTextChanger.bind(wrapper);
            }).filter(isNotNil);
            this.responseText = "";
            this._openState = true;
            function finish(method2, url2) {
              return self2._realxhr.open(method2, url2, self2._connection.async);
            }
            if (this._connection.async) {
              this._requestChangers = this._activeWrappers.map((wrapper) => {
                return wrapper.requestChanger && wrapper.requestChanger.bind(wrapper);
              }).filter(isNotNil);
              if (this._requestChangers.length) {
                if (this.readyState != 1) {
                  this.readyState = 1;
                  this._fakeRscEvent();
                }
              } else {
                finish(method, url);
              }
            } else {
              finish(method, url);
            }
          };
          XHRProxy.prototype.send = function(body) {
            var self2 = this;
            this._clientStartedSend = true;
            this._openState = false;
            Object.defineProperty(this._connection, "originalSendBody", {
              enumerable: true,
              writable: false,
              configurable: false,
              value: body
            });
            this._connection.responseType = this._realxhr.responseType || "text";
            each_default()(self2._activeWrappers, function(wrapper) {
              if (wrapper.originalSendBodyLogger) {
                try {
                  wrapper.originalSendBodyLogger(self2._connection, body);
                } catch (e) {
                  logError(e);
                }
              }
            });
            function finish(body2) {
              self2._realStartedSend = true;
              self2._realxhr.send(body2);
            }
            if (this._connection.async && this._requestChangers.length) {
              const startConnection = this._connection;
              const request = {
                method: this._connection.method,
                url: this._connection.url,
                body
              };
              (async () => {
                let modifiedRequest = request;
                for (const requestChanger of this._requestChangers) {
                  const longRunWarningTimer = setTimeout(() => {
                    console.warn("requestChanger is taking too long", requestChanger, startConnection);
                  }, WARNING_TIMEOUT);
                  try {
                    modifiedRequest = await requestChanger(this._connection, Object.freeze(modifiedRequest));
                  } finally {
                    clearTimeout(longRunWarningTimer);
                  }
                  (0, assert.v)(has_default()(modifiedRequest, "method"), "modifiedRequest has method");
                  (0, assert.v)(has_default()(modifiedRequest, "url"), "modifiedRequest has url");
                  (0, assert.v)(has_default()(modifiedRequest, "body"), "modifiedRequest has body");
                  if (startConnection !== this._connection || this._realStartedSend)
                    break;
                }
                return modifiedRequest;
              })().catch((err) => {
                logError(err);
                return request;
              }).then((modifiedRequest) => {
                if (startConnection === this._connection && !this._realStartedSend) {
                  this._realxhr.open(modifiedRequest.method, modifiedRequest.url);
                  this._events.emit("realOpen");
                  finish(modifiedRequest.body);
                }
              });
            } else {
              finish(body);
            }
          };
          [XHRProxy, XHRProxy.prototype].forEach(function(obj) {
            Object.assign(obj, {
              UNSENT: 0,
              OPENED: 1,
              HEADERS_RECEIVED: 2,
              LOADING: 3,
              DONE: 4
            });
          });
          return XHRProxy;
        }
        var gmail_response_processor = __webpack_require__2(1433);
        const ThreadRowAd = Symbol(`ThreadRowAd`);
        function extractMetadataFromThreadRow(threadRow) {
          var timeSpan, subjectSpan, peopleDiv;
          (0, assert.v)(threadRow.hasAttribute("id"), "check element is main thread row");
          var errors = [];
          var threadRowIsVertical = intersection_default()(Array.from(threadRow.classList), ["zA", "apv"]).length === 2;
          const isThreadRowAd = threadRow.querySelector(".am0,.bvA");
          if (isThreadRowAd) {
            return ThreadRowAd;
          } else if (threadRowIsVertical) {
            var threadRow2 = threadRow.nextElementSibling;
            if (!threadRow2) {
              errors.push("failed to find threadRow2");
            } else {
              var threadRow3 = threadRow2.nextElementSibling;
              if (!threadRow3 || !threadRow3.classList.contains("apw")) {
                threadRow3 = null;
              }
              timeSpan = threadRow.querySelector("td.apt > div.apm > span[title]");
              subjectSpan = threadRow2.querySelector("td div.xS div.xT div.y6 > span");
              peopleDiv = threadRow.querySelector("td.apy > div.yW, td.apx > div.yW");
            }
          } else {
            timeSpan = threadRow.querySelector("td.xW > span[title]");
            var subjectAreaDiv = threadRow.querySelector("td.a4W div[role=link] div.y6");
            if (subjectAreaDiv && subjectAreaDiv.children.length >= 1) {
              subjectSpan = subjectAreaDiv.children[0];
            }
            peopleDiv = threadRow.querySelector("td.yX > div.yW");
          }
          if (!timeSpan) {
            errors.push("failed to find timeSpan");
          }
          if (!subjectSpan) {
            errors.push("failed to find subjectSpan");
          }
          if (!peopleDiv) {
            errors.push("failed to find peopleDiv");
          }
          if (errors.length) {
            injected_logger.error(new Error("Errors in thread row parsing"), {
              errors
            });
          }
          return {
            timeString: timeSpan ? timeSpan.getAttribute("title") || "" : "",
            subject: subjectSpan ? subjectSpan.textContent : "",
            peopleHtml: peopleDiv ? (0, gmail_response_processor.On)(peopleDiv.innerHTML) : ""
          };
        }
        var constant = __webpack_require__2(7660);
        var constant_default = /* @__PURE__ */ __webpack_require__2.n(constant);
        const ignoreErrors = constant_default()(true);
        function getIfOwn(object, prop) {
          if (Object.prototype.hasOwnProperty.call(object, prop)) {
            return object[prop];
          }
          return null;
        }
        function clickAndGetPopupUrl(element) {
          const event = document.createEvent("MouseEvents");
          const options = {
            bubbles: true,
            cancelable: true,
            button: 0,
            pointerX: 0,
            pointerY: 0,
            ctrlKey: true,
            altKey: false,
            shiftKey: false,
            metaKey: true
          };
          event.initMouseEvent("click", options.bubbles, options.cancelable, document.defaultView, options.button, options.pointerX, options.pointerY, options.pointerX, options.pointerY, options.ctrlKey, options.altKey, options.shiftKey, options.metaKey, options.button, null);
          let url;
          const { open: oldWindowOpen, onerror: oldWindowOnerror } = window, oldFocus = getIfOwn(window.HTMLElement.prototype, "focus"), oldBlur = getIfOwn(window.HTMLElement.prototype, "blur");
          try {
            window.HTMLElement.prototype.focus = noop_default();
            window.HTMLElement.prototype.blur = noop_default();
            window.onerror = ignoreErrors;
            const newOpen = function(_url, _title, _options) {
              url = _url;
              const newWin = {
                closed: false,
                focus: noop_default()
              };
              setTimeout(function() {
                newWin.closed = true;
              }, 5);
              return newWin;
            };
            window.open = newOpen;
            if (window.open !== newOpen) {
              injected_logger.error(new Error("Failed to override window.open"));
              return null;
            }
            element.dispatchEvent(event);
          } finally {
            if (oldFocus) {
              window.HTMLElement.prototype.focus = oldFocus;
            } else {
              delete window.HTMLElement.prototype.focus;
            }
            if (oldBlur) {
              window.HTMLElement.prototype.blur = oldBlur;
            } else {
              delete window.HTMLElement.prototype.blur;
            }
            window.onerror = oldWindowOnerror;
            window.open = oldWindowOpen;
          }
          return url;
        }
        function findParent(el, cb) {
          let candidate = el.parentElement;
          while (candidate) {
            if (cb(candidate)) {
              return candidate;
            }
            candidate = candidate.parentElement;
          }
          return null;
        }
        var CustomDomEvent = {
          tellMeThisThreadIdByDatabase: "inboxSDKtellMeThisThreadIdByDatabase",
          tellMeThisThreadIdByClick: "inboxSDKtellMeThisThreadIdByClick"
        };
        function setup() {
          try {
            processPreloadedThreads();
          } catch (err) {
            injected_logger.error(err, "Failed to process preloaded thread identifiers");
          }
          document.addEventListener(CustomDomEvent.tellMeThisThreadIdByDatabase, function(event) {
            try {
              if (!(event.target instanceof HTMLElement)) {
                throw new Error("event.target is not an HTMLElement");
              }
              const threadId = getGmailThreadIdForThreadRowByDatabase(event.target);
              if (threadId) {
                event.target.setAttribute("data-inboxsdk-threadid", threadId);
              }
            } catch (err) {
              injected_logger.error(err, "Error in inboxSDKtellMeThisThreadIdByDatabase");
            }
          });
          document.addEventListener(CustomDomEvent.tellMeThisThreadIdByClick, function(event) {
            try {
              if (!(event.target instanceof HTMLElement)) {
                throw new Error("event.target is not an HTMLElement");
              }
              const threadId = getGmailThreadIdForThreadRowByClick(event.target);
              if (threadId) {
                event.target.setAttribute("data-inboxsdk-threadid", threadId);
              }
            } catch (err) {
              injected_logger.error(err, "Error in inboxSDKtellMeThisThreadIdByClick");
            }
          });
        }
        function processThreadListResponse(threadListResponse) {
          processThreads(gmail_response_processor.rq(threadListResponse));
        }
        function processThreads(threads) {
          threads.forEach(storeThreadMetadata);
        }
        const AMBIGUOUS = {
          name: "AMBIGUOUS"
        };
        const threadIdsByKey = new Map;
        function storeThreadMetadata(threadMetadata) {
          var key = threadMetadataKey(threadMetadata);
          if (threadIdsByKey.has(key)) {
            if (threadIdsByKey.get(key) !== threadMetadata.gmailThreadId) {
              threadIdsByKey.set(key, AMBIGUOUS);
            }
          } else {
            threadIdsByKey.set(key, threadMetadata.gmailThreadId);
          }
        }
        function threadMetadataKey(threadRowMetadata) {
          return threadRowMetadata.subject.trim() + ":" + threadRowMetadata.timeString.trim() + ":" + threadRowMetadata.peopleHtml.trim();
        }
        function processPreloadedThreads() {
          const preloadScript = find_default()(document.querySelectorAll("script:not([src])"), (script) => script.text && script.text.slice(0, 500).indexOf("var VIEW_DATA=[[") > -1);
          if (!preloadScript) {
            return;
          } else {
            const firstBracket = preloadScript.text.indexOf("[");
            const lastBracket = preloadScript.text.lastIndexOf("]");
            const viewDataString = preloadScript.text.slice(firstBracket, lastBracket + 1);
            processThreads(gmail_response_processor.eF([gmail_response_processor.XX(viewDataString)]));
          }
        }
        function getThreadIdFromUrl(url) {
          var tid = (0, querystring_es3.parse)(url).th;
          if (!tid) {
            var urlHashMatch = url.match(/#(.*)/);
            if (urlHashMatch) {
              url = decodeURIComponent(decodeURIComponent(urlHashMatch[1]));
              tid = (0, querystring_es3.parse)(url).th;
            }
          }
          return tid.replace("#", "");
        }
        function getGmailThreadIdForThreadRowByDatabase(threadRow) {
          const domRowMetadata = extractMetadataFromThreadRow(threadRow);
          if (domRowMetadata === ThreadRowAd) {
            return;
          }
          const key = threadMetadataKey(domRowMetadata);
          const value = threadIdsByKey.get(key);
          if (typeof value === "string") {
            return value;
          }
        }
        function getGmailThreadIdForThreadRowByClick(threadRow) {
          extractMetadataFromThreadRow(threadRow);
          const parent = findParent(threadRow, (el) => el.nodeName === "DIV" && el.getAttribute("role") === "main");
          if (!parent) {
            throw new Error("Can't operate on disconnected thread row");
          }
          const currentRowSelection = parent.querySelector("td.PE") || parent.querySelector("tr");
          const url = clickAndGetPopupUrl(threadRow);
          const threadId = url && getThreadIdFromUrl(url);
          if (currentRowSelection) {
            clickAndGetPopupUrl(currentRowSelection);
          }
          return threadId;
        }
        var startsWith = __webpack_require__2(7013);
        var startsWith_default = /* @__PURE__ */ __webpack_require__2.n(startsWith);
        var gmailAjax = __webpack_require__2(5609);
        function extractThreadsFromSearchResponse(response) {
          const parsedResponse = JSON.parse(response);
          if (Array.isArray(parsedResponse)) {
            try {
              return extractThreadsFromSearchResponse_20220909(parsedResponse);
            } catch (err) {
              return [];
            }
          }
          const threadDescriptors = parsedResponse && parsedResponse[3];
          if (!threadDescriptors)
            return [];
          return threadDescriptors.map((descriptorWrapper, index) => {
            const descriptor = descriptorWrapper[1];
            if (!descriptor)
              return null;
            return {
              subject: descriptor[1],
              snippet: descriptor[2],
              syncThreadID: descriptor[4],
              oldGmailThreadID: descriptor[18] != null ? new (bignumber_default())(descriptor[18]).toString(16) : descriptor[20],
              rawResponse: descriptorWrapper,
              extraMetaData: {
                snippet: parsedResponse[15] && parsedResponse[15][1] && parsedResponse[15][1][index] || "",
                syncMessageData: descriptor[5].map((md) => ({
                  syncMessageID: md[1],
                  oldMessageID: md[56],
                  date: +md[7]
                }))
              }
            };
          }).filter(isNotNil);
        }
        function extractThreadsFromSearchResponse_20220909(parsedResponse) {
          const threadDescriptors = parsedResponse && parsedResponse[2];
          if (!threadDescriptors)
            return [];
          return threadDescriptors.map((descriptorWrapper, index) => {
            const descriptor = descriptorWrapper[0];
            if (!descriptor)
              return null;
            return {
              subject: descriptor[0],
              snippet: descriptor[1],
              syncThreadID: descriptor[3],
              oldGmailThreadID: descriptor[17] != null ? new (bignumber_default())(descriptor[17]).toString(16) : descriptor[19],
              rawResponse: descriptorWrapper,
              extraMetaData: {
                snippet: parsedResponse[14] && parsedResponse[14][0] && parsedResponse[14][0][index] || "",
                syncMessageData: descriptor[4].map((md) => ({
                  syncMessageID: md[0],
                  oldMessageID: md[55],
                  date: +md[6]
                }))
              }
            };
          }).filter(isNotNil);
        }
        function extractThreadsFromThreadResponse(response) {
          const parsedResponse = JSON.parse(response);
          if (Array.isArray(parsedResponse)) {
            return extractThreadsFromThreadResponse_20220909(parsedResponse);
          }
          const threadDescriptors = parsedResponse && parsedResponse[2];
          if (!threadDescriptors)
            throw new Error("Failed to process thread response");
          return threadDescriptors.map((descriptorWrapper) => {
            if (typeof descriptorWrapper[1] === "string" && Array.isArray(descriptorWrapper[3]) && !(descriptorWrapper[2] && descriptorWrapper[2][1] && descriptorWrapper[2][1][14] && Array.isArray(descriptorWrapper[2][2]))) {
              return {
                syncThreadID: descriptorWrapper[1],
                oldGmailThreadID: descriptorWrapper[2] && descriptorWrapper[2][1] && descriptorWrapper[2][1][16] || undefined,
                extraMetaData: {
                  snippet: descriptorWrapper[2] && descriptorWrapper[2][1] && descriptorWrapper[2][1][3] || undefined,
                  syncMessageData: (descriptorWrapper[3] || []).filter((md) => Boolean(md[2])).map((md) => ({
                    syncMessageID: md[1],
                    date: +md[2][17],
                    recipients: getRecipientsFromMessageDescriptor(md)
                  }))
                }
              };
            } else {
              const threadDescriptor = descriptorWrapper[2] && descriptorWrapper[2][1];
              if (!threadDescriptor)
                return null;
              let syncMessageData;
              const fullMessageDescriptors = Array.isArray(descriptorWrapper[3]) && descriptorWrapper[3];
              if (fullMessageDescriptors) {
                syncMessageData = fullMessageDescriptors.map((md) => ({
                  syncMessageID: md[1],
                  date: +md[2][17],
                  recipients: getRecipientsFromMessageDescriptor(md)
                }));
              } else {
                const messageDescriptors = descriptorWrapper[2] && descriptorWrapper[2][2];
                syncMessageData = messageDescriptors.map((md) => ({
                  syncMessageId: md[1],
                  date: +md[16]
                }));
              }
              return {
                subject: threadDescriptor[2],
                snippet: threadDescriptor[3],
                syncThreadID: threadDescriptor[1],
                oldGmailThreadID: new (bignumber_default())(threadDescriptor[14]).toString(16),
                rawResponse: descriptorWrapper,
                extraMetaData: {
                  syncMessageData,
                  snippet: ""
                }
              };
            }
          }).filter(isNotNil);
        }
        function extractThreadsFromThreadResponse_20220909(parsedResponse) {
          const threadDescriptors = parsedResponse && parsedResponse[1];
          if (!threadDescriptors)
            throw new Error("Failed to process thread response");
          return threadDescriptors.map((descriptorWrapper) => {
            if (typeof descriptorWrapper[0] === "string" && Array.isArray(descriptorWrapper[2]) && !(descriptorWrapper[1] && descriptorWrapper[1][0] && descriptorWrapper[1][0][13] && Array.isArray(descriptorWrapper[1][1]))) {
              return {
                syncThreadID: descriptorWrapper[0],
                oldGmailThreadID: descriptorWrapper[1] && descriptorWrapper[1][0] && descriptorWrapper[1][0][15] || undefined,
                extraMetaData: {
                  snippet: descriptorWrapper[1] && descriptorWrapper[1][0] && descriptorWrapper[1][0][2] || undefined,
                  syncMessageData: (descriptorWrapper[2] || []).filter((md) => Boolean(md[1])).map((md) => ({
                    syncMessageID: md[0],
                    date: +md[1][16],
                    recipients: getRecipientsFromMessageDescriptor_20220909(md)
                  }))
                }
              };
            } else {
              const threadDescriptor = descriptorWrapper[1] && descriptorWrapper[1][0];
              if (!threadDescriptor)
                return null;
              let syncMessageData;
              const fullMessageDescriptors = Array.isArray(descriptorWrapper[2]) && descriptorWrapper[2];
              if (fullMessageDescriptors) {
                syncMessageData = fullMessageDescriptors.map((md) => ({
                  syncMessageID: md[0],
                  date: +md[1][16],
                  recipients: getRecipientsFromMessageDescriptor_20220909(md)
                }));
              } else {
                const messageDescriptors = descriptorWrapper[1] && descriptorWrapper[1][1];
                syncMessageData = messageDescriptors.map((md) => ({
                  syncMessageId: md[0],
                  date: +md[15]
                }));
              }
              return {
                subject: threadDescriptor[1],
                snippet: threadDescriptor[2],
                syncThreadID: threadDescriptor[0],
                oldGmailThreadID: new (bignumber_default())(threadDescriptor[13]).toString(16),
                rawResponse: descriptorWrapper,
                extraMetaData: {
                  syncMessageData,
                  snippet: ""
                }
              };
            }
          }).filter(isNotNil);
        }
        function getRecipientsFromMessageDescriptor(messageDescriptor) {
          if (!messageDescriptor[2])
            return;
          const to = messageDescriptor[2][1] || [];
          const cc = messageDescriptor[2][2] || [];
          const bcc = messageDescriptor[2][3] || [];
          return to.concat(cc).concat(bcc).map((recipientDescriptor) => ({
            emailAddress: recipientDescriptor[2],
            name: recipientDescriptor[3]
          }));
        }
        function getRecipientsFromMessageDescriptor_20220909(messageDescriptor) {
          if (!messageDescriptor[1])
            return;
          const to = messageDescriptor[1][0] || [];
          const cc = messageDescriptor[1][1] || [];
          const bcc = messageDescriptor[1][2] || [];
          return to.concat(cc).concat(bcc).map((recipientDescriptor) => ({
            emailAddress: recipientDescriptor[1],
            name: recipientDescriptor[2]
          }));
        }
        function replaceThreadsInSearchResponse(response, replacementThreads, _unused) {
          const parsedResponse = JSON.parse(response);
          if (Array.isArray(parsedResponse)) {
            try {
              return replaceThreadsInSearchResponse_20220909(parsedResponse, replacementThreads, _unused);
            } catch (err) {
              console.error("Caught err in replaceThreadsInSearchResponse", err);
              return response;
            }
          }
          if (parsedResponse[3] || replacementThreads.length) {
            parsedResponse[3] = replacementThreads.map((_ref, index) => {
              let {
                rawResponse
              } = _ref;
              return {
                ...rawResponse,
                "2": index
              };
            });
          }
          if (parsedResponse[15] || replacementThreads.length) {
            parsedResponse[15] = {
              ...parsedResponse[15],
              "1": replacementThreads.map((_ref2) => {
                let {
                  extraMetaData
                } = _ref2;
                return extraMetaData.snippet;
              }),
              "2": replacementThreads.map((_ref3) => {
                let {
                  extraMetaData
                } = _ref3;
                return extraMetaData.syncMessageData.map((_ref4) => {
                  let {
                    syncMessageID
                  } = _ref4;
                  return syncMessageID;
                });
              })
            };
          }
          return JSON.stringify(parsedResponse);
        }
        function replaceThreadsInSearchResponse_20220909(parsedResponse, replacementThreads, _unused) {
          if (parsedResponse[2] || replacementThreads.length) {
            parsedResponse[2] = replacementThreads.map((_ref5, index) => {
              let {
                rawResponse
              } = _ref5;
              const res = [...rawResponse];
              res[1] = index;
              return res;
            });
          }
          if (parsedResponse[14] || replacementThreads.length) {
            parsedResponse[14] = [...parsedResponse[14]];
            parsedResponse[14][0] = replacementThreads.map((_ref6) => {
              let {
                extraMetaData
              } = _ref6;
              return extraMetaData.snippet;
            });
            if (Array.isArray(parsedResponse[14][1]) && parsedResponse[14][1].length > 0 && Array.isArray(parsedResponse[14][1][0][0])) {
              parsedResponse[14][1] = replacementThreads.map((_ref7) => {
                let {
                  extraMetaData
                } = _ref7;
                return [[extraMetaData.syncMessageData[0].syncMessageID]];
              });
            } else {
              parsedResponse[14][1] = replacementThreads.map((_ref8) => {
                let {
                  extraMetaData
                } = _ref8;
                return extraMetaData.syncMessageData.map((_ref9) => {
                  let {
                    syncMessageID
                  } = _ref9;
                  return syncMessageID;
                });
              });
            }
          }
          return JSON.stringify(parsedResponse);
        }
        var getAccountUrlPart = __webpack_require__2(8105);
        async function getThreadFromSyncThreadId(driver, syncThreadId) {
          const [btaiHeader, xsrfToken] = await Promise.all([driver.getPageCommunicator().getBtaiHeader(), driver.getPageCommunicator().getXsrfToken()]);
          return getThreadFromSyncThreadIdUsingHeaders(syncThreadId, btaiHeader, xsrfToken);
        }
        async function getThreadFromSyncThreadIdUsingHeaders(syncThreadId, btaiHeader, xsrfToken) {
          let responseText = null;
          try {
            const {
              text
            } = await (0, gmailAjax.A)({
              method: "POST",
              url: `https://mail.google.com/sync${(0, getAccountUrlPart.A)()}/i/fd`,
              headers: {
                "Content-Type": "application/json",
                "X-Framework-Xsrf-Token": xsrfToken,
                "X-Gmail-BTAI": btaiHeader,
                "X-Google-BTD": "1"
              },
              data: JSON.stringify({
                "1": [{
                  "1": syncThreadId,
                  "2": 1
                }]
              })
            });
            responseText = text;
          } catch (err) {
            const {
              text
            } = await (0, gmailAjax.A)({
              method: "POST",
              url: `https://mail.google.com/sync${(0, getAccountUrlPart.A)()}/i/fd?rt=r&pt=ji`,
              headers: {
                "Content-Type": "application/json",
                "X-Framework-Xsrf-Token": xsrfToken,
                "X-Gmail-BTAI": btaiHeader,
                "X-Google-BTD": "1"
              },
              data: JSON.stringify([[[syncThreadId, 1]], 2])
            });
            responseText = text;
          }
          const threadDescriptors = extractThreadsFromThreadResponse(responseText);
          if (threadDescriptors.length > 0) {
            const thread = threadDescriptors[0];
            if (thread.oldGmailThreadID) {
              return thread;
            }
          }
          return null;
        }
        var requestGmailThread = __webpack_require__2(5355);
        const threadIdToMessages = new Map;
        function message_metadata_holder_setup() {
          document.addEventListener("inboxSDKtellMeThisMessageDate", function(event) {
            exposeMetadata(event, "data-inboxsdk-sortdate", (m) => m.date);
          });
          document.addEventListener("inboxSDKtellMeThisMessageRecipients", function(event) {
            exposeMetadata(event, "data-inboxsdk-recipients", (m) => {
              if (m.recipients)
                return m.recipients;
              else
                return null;
            });
          });
        }
        function exposeMetadata(event, attribute, processor) {
          const {
            target,
            detail: {
              threadId,
              ikValue,
              btaiHeader,
              xsrfToken
            }
          } = event;
          (async () => {
            const messageIndex = Array.from(target.parentElement.children).filter((el) => !el.classList.contains("inboxsdk__custom_message_view")).indexOf(target);
            if (messageIndex < 0) {
              throw new Error("Should not happen");
            }
            let message = getMessage(threadId, messageIndex);
            if (message == null || !message.recipients) {
              try {
                await addDataForThread(threadId, ikValue, btaiHeader, xsrfToken);
              } catch (err) {
                injected_logger.error(err);
              }
              message = getMessage(threadId, messageIndex);
              if (message == null) {
                throw new Error("Failed to find message date after re-requesting thread");
              }
            }
            target.setAttribute(attribute, JSON.stringify(processor(message)));
          })().catch((err) => {
            target.setAttribute(attribute, "error");
            injected_logger.error(err);
          });
        }
        function getMessage(threadId, messageIndex) {
          const messages = threadIdToMessages.get(threadId);
          if (messages) {
            const message = messages[messageIndex];
            if (message) {
              return message;
            }
          }
        }
        function add(groupedMessages) {
          groupedMessages.forEach((group) => {
            if (group.syncThreadID != null) {
              threadIdToMessages.set(group.syncThreadID, group.messages);
            }
            threadIdToMessages.set(group.threadID, group.messages);
          });
        }
        const activeThreadRequestPromises = new Map;
        function addDataForThread(threadId, ikValue, btaiHeader, xsrfToken) {
          const existingRequestPromise = activeThreadRequestPromises.get(threadId);
          if (existingRequestPromise) {
            return existingRequestPromise;
          }
          const newPromise = (async () => {
            try {
              if (startsWith_default()(threadId, "thread")) {
                if (!btaiHeader || !xsrfToken) {
                  throw new Error("Need btaiHeader and xsrfToken when in new data layer");
                }
                const syncThread = await getThreadFromSyncThreadIdUsingHeaders(threadId, btaiHeader, xsrfToken);
                if (syncThread) {
                  add([{
                    syncThreadID: syncThread.syncThreadID,
                    threadID: syncThread.oldGmailThreadID,
                    messages: syncThread.extraMetaData.syncMessageData.map((syncMessage) => ({
                      date: syncMessage.date,
                      recipients: syncMessage.recipients
                    }))
                  }]);
                }
              } else {
                const text = await (0, requestGmailThread.A)(ikValue, threadId);
                add((0, gmail_response_processor.St)(text));
              }
            } catch (err) {
              injected_logger.error(err);
            } finally {
              activeThreadRequestPromises.delete(threadId);
            }
          })();
          activeThreadRequestPromises.set(threadId, newPromise);
          return newPromise;
        }
        function quotedSplit(s) {
          let split = [];
          let lastEnd = 0;
          const quoteRe = /"[^"]*"/g;
          while (true) {
            const match = quoteRe.exec(s);
            split = split.concat((match ? s.substring(lastEnd, match.index) : s.substring(lastEnd)).split(/ +/).filter(Boolean));
            if (!match)
              break;
            lastEnd = match.index + match[0].length;
            split.push(match[0]);
          }
          return split;
        }
        function defer() {
          let resolve = undefined;
          let reject = undefined;
          const promise = new Promise((_resolve, _reject) => {
            resolve = _resolve;
            reject = _reject;
          });
          return {
            resolve,
            reject,
            promise
          };
        }
        var modify_suggestions = __webpack_require__2(8700);
        var sortBy = __webpack_require__2(3281);
        var sortBy_default = /* @__PURE__ */ __webpack_require__2.n(sortBy);
        const SEND_ACTIONS = ["^pfg"];
        const DRAFT_SAVING_ACTIONS = ["^r", "^r_bt"];
        const ACTION_TYPE_PRIORITY_RANK = ["SEND", "DRAFT_SAVE"];
        function parseComposeRequestBody_2022_09_09(request) {
          return parseCreateUpdateSendDraftRequestBody(request);
        }
        function parseComposeResponseBody_2022_09_09(response) {
          return parseCreateUpdateSendDraftResponseBody(response);
        }
        function replaceBodyContentInComposeSendRequestBody_2022_09_09(request, newBodyHtmlContent) {
          return replaceBodyContentInSendRequestBody(request, newBodyHtmlContent);
        }
        function parseCreateUpdateSendDraftRequestBody(request) {
          const updateList = request[1]?.[0];
          if (!Array.isArray(updateList)) {
            return null;
          }
          const parsedMessages = updateList.map(parseRequestThread).filter(isNotNil);
          const sorted = sortBy_default()(parsedMessages, (m) => ACTION_TYPE_PRIORITY_RANK.indexOf(m.type));
          return sorted[0] || null;
        }
        function parseCreateUpdateSendDraftResponseBody(response) {
          const updateList = response[1]?.[5];
          if (!Array.isArray(updateList)) {
            return [];
          }
          return updateList.map(parseResponseThread).filter(isNotNil).flatMap((parsedThread) => {
            const {
              threadId,
              oldThreadId,
              parsedMessages
            } = parsedThread;
            return parsedMessages.map((parsedMessage) => {
              const {
                messageId,
                to,
                cc,
                bcc,
                actions,
                rfcID,
                oldMessageId
              } = parsedMessage;
              const actionType = actionsToComposeRequestType(actions);
              if (!actionType) {
                return null;
              }
              return {
                threadId,
                messageId,
                to,
                cc,
                bcc,
                actions,
                rfcID,
                oldMessageId,
                oldThreadId,
                type: actionType
              };
            });
          }).filter(isNotNil);
        }
        function replaceBodyContentInSendRequestBody(request, newBodyHtmlContent) {
          const parsed = parseCreateUpdateSendDraftRequestBody(request);
          if (!parsed) {
            return null;
          }
          const replaceBodyInThisMessageId = parsed.messageId;
          const updateList = request[1]?.[0];
          if (!Array.isArray(updateList)) {
            return null;
          }
          for (const threadWrapper of updateList) {
            if (!Array.isArray(threadWrapper) || !Array.isArray(threadWrapper[1])) {
              return null;
            }
            const thread = threadWrapper[1];
            const threadId = parseThreadId(thread[0]);
            if (!threadId) {
              return null;
            }
            const parseResult = findAndParseRequestMessage(thread);
            if (parseResult?.parsedMsg.messageId === replaceBodyInThisMessageId) {
              const actionType = actionsToComposeRequestType(parseResult.parsedMsg.actions);
              if (actionType === "SEND") {
                replaceBodyInRequestMsg(parseResult.originalMsg, newBodyHtmlContent);
                return request;
              }
            }
          }
          return null;
        }
        function parseThreadId(threadId) {
          if (!threadId.startsWith("thread-")) {
            return null;
          }
          if (threadId.includes("|")) {
            return threadId.split("|")[0];
          }
          return threadId;
        }
        function parseMsgId(messageId) {
          if (!messageId.startsWith("msg-")) {
            return null;
          }
          return messageId;
        }
        function parseContacts(contacts) {
          if (!Array.isArray(contacts)) {
            return null;
          }
          return contacts.filter((c) => !!c[1]).map((c) => ({
            emailAddress: c[1],
            name: c[2] ?? null
          }));
        }
        function findAndParseRequestMessage(thread) {
          const originalMsgs = [thread[1]?.[2]?.[0]?.[4]?.[0], thread[1]?.[1]?.[0], thread[1]?.[13]?.[0]];
          for (const originalMsg of originalMsgs) {
            const parsedMsg = parseRequestMsg(originalMsg);
            if (parsedMsg) {
              return {
                parsedMsg,
                originalMsg
              };
            }
          }
          return null;
        }
        function parseRequestThread(threadWrapper) {
          if (!Array.isArray(threadWrapper) || !Array.isArray(threadWrapper[1])) {
            return null;
          }
          const thread = threadWrapper[1];
          const threadId = parseThreadId(thread[0]);
          if (!threadId) {
            return null;
          }
          const parseResult = findAndParseRequestMessage(thread);
          if (!parseResult) {
            return null;
          }
          const {
            parsedMsg: message,
            originalMsg
          } = parseResult;
          const {
            messageId,
            to,
            cc,
            bcc,
            subject,
            body,
            actions
          } = message;
          let actionType = actionsToComposeRequestType(actions);
          if (!actionType) {
            return null;
          }
          if (actionType === "DRAFT_SAVE" && (originalMsg === thread[1]?.[2]?.[0]?.[4]?.[0] || originalMsg === thread[1]?.[1]?.[0])) {
            actionType = "FIRST_DRAFT_SAVE";
          }
          return {
            threadId,
            messageId,
            to,
            cc,
            bcc,
            subject,
            body,
            actions,
            type: actionType
          };
        }
        function parseRequestMsg(msg) {
          if (!Array.isArray(msg)) {
            return null;
          }
          const messageId = parseMsgId(msg[0]);
          if (!messageId) {
            return null;
          }
          const subject = msg[7];
          const to = parseContacts(msg[2]);
          const cc = parseContacts(msg[3]);
          const bcc = parseContacts(msg[4]);
          const body = msg[8][1][0][1];
          const actions = msg[10];
          const rfcID = msg[13];
          const oldMessageId = msg[55];
          return {
            messageId,
            to,
            cc,
            bcc,
            subject,
            body,
            actions,
            rfcID,
            oldMessageId
          };
        }
        function replaceBodyInRequestMsg(msg, newBodyHtmlContent) {
          if (!Array.isArray(msg)) {
            return null;
          }
          msg[8][1][0][1] = newBodyHtmlContent;
        }
        function parseResponseThread(threadWrapper) {
          if (!Array.isArray(threadWrapper) || !Array.isArray(threadWrapper[0])) {
            return null;
          }
          const thread = threadWrapper[0];
          const threadId = parseThreadId(thread[0]);
          if (!threadId) {
            return null;
          }
          const threadInner = thread[2]?.[6]?.[0];
          if (!Array.isArray(threadInner)) {
            return null;
          }
          const oldThreadId = threadInner[19];
          const parsedMessages = Array.isArray(threadInner[4]) ? threadInner[4].map((msg) => {
            if (!Array.isArray(msg)) {
              return null;
            }
            return parseResponseMsg(msg);
          }).filter(isNotNil) : [];
          return {
            threadId,
            oldThreadId,
            parsedMessages
          };
        }
        function parseResponseMsg(msg) {
          if (!Array.isArray(msg)) {
            return null;
          }
          const messageId = parseMsgId(msg[0]);
          if (!messageId) {
            return null;
          }
          const actions = msg[10];
          const to = parseContacts(msg[2]);
          const cc = parseContacts(msg[3]);
          const bcc = parseContacts(msg[4]);
          const rfcID = msg[13];
          const oldMessageId = msg[55];
          return {
            messageId,
            to,
            cc,
            bcc,
            actions,
            rfcID,
            oldMessageId
          };
        }
        function actionsToComposeRequestType(actions) {
          if (intersection_default()(actions, DRAFT_SAVING_ACTIONS).length === DRAFT_SAVING_ACTIONS.length) {
            return "DRAFT_SAVE";
          }
          if (intersection_default()(actions, SEND_ACTIONS).length === SEND_ACTIONS.length) {
            return "SEND";
          }
          return null;
        }
        function getDetailsOfComposeRequest(parsed) {
          const updateList = parsed[2] && parsed[2][1];
          if (!updateList)
            return null;
          const messageUpdates = updateList.filter((update) => {
            const updateWrapper = update[2] && update[2][2] && (update[2][2][14] || update[2][2][2]);
            return updateWrapper && updateWrapper[1] && updateWrapper[1][1] && updateWrapper[1][1].indexOf("msg-a:") > -1;
          });
          if (messageUpdates.length) {
            const sendUpdateMatch = messageUpdates.find((update) => {
              const updateWrapper = update[2] && update[2][2] && (update[2][2][14] || update[2][2][2]);
              return updateWrapper[1][11] && intersection_default()(updateWrapper[1][11], SEND_ACTIONS).length === SEND_ACTIONS.length;
            });
            if (sendUpdateMatch) {
              const sendUpdateWrapper = sendUpdateMatch[2] && sendUpdateMatch[2][2] && (sendUpdateMatch[2][2][14] || sendUpdateMatch[2][2][2]);
              const sendUpdate = sendUpdateWrapper[1];
              return getComposeRequestFromUpdate(sendUpdate, "SEND");
            } else {
              const firstMessageUpdate = messageUpdates[0];
              const updateWrapper = firstMessageUpdate[2] && firstMessageUpdate[2][2] && (firstMessageUpdate[2][2][14] || firstMessageUpdate[2][2][2]);
              const update = updateWrapper[1];
              return getComposeRequestFromUpdate(update, "DRAFT_SAVE");
            }
          } else {
            const messageUpdates2 = updateList.map((update) => update[2] && update[2][2] && update[2][2][3] && update[2][2][3][1] && update[2][2][3][1][5] && update[2][2][3][1][5][0]).filter(Boolean);
            if (messageUpdates2.length === 0)
              return null;
            const firstMessageUpdate = messageUpdates2[0];
            return getComposeRequestFromUpdate(firstMessageUpdate, "FIRST_DRAFT_SAVE");
          }
        }
        function getComposeRequestFromUpdate(update, type) {
          const body = update[9] && update[9][2] && update[9][2][0] && update[9][2][0][2];
          if (body == null)
            return null;
          return {
            body,
            type,
            to: sync_compose_request_processor_parseContacts(update[3]),
            cc: sync_compose_request_processor_parseContacts(update[4]),
            bcc: sync_compose_request_processor_parseContacts(update[5]),
            draftID: update[1].replace("msg-a:", ""),
            subject: update[8]
          };
        }
        function sync_compose_request_processor_parseContacts(contacts) {
          if (!Array.isArray(contacts)) {
            return null;
          }
          return contacts.map((c) => ({
            emailAddress: c[2],
            name: c[3] || null
          }));
        }
        function replaceEmailBodyForSendRequest(request, newBody) {
          if (!newBody)
            return request;
          const parsed = JSON.parse(request);
          const updateList = parsed[2] && parsed[2][1];
          if (!updateList)
            return request;
          const messageUpdates = updateList.filter((update) => {
            const updateWrapper = update[2] && update[2][2] && (update[2][2][14] || update[2][2][2]);
            return updateWrapper && updateWrapper[1] && updateWrapper[1][1] && updateWrapper[1][1].indexOf("msg-a:") > -1;
          });
          if (!messageUpdates.length)
            return request;
          const sendUpdateMatch = messageUpdates.find((update) => {
            const updateWrapper = update[2] && update[2][2] && (update[2][2][14] || update[2][2][2]);
            return updateWrapper[1][11] && intersection_default()(updateWrapper[1][11], SEND_ACTIONS).length === SEND_ACTIONS.length;
          });
          if (!sendUpdateMatch)
            return request;
          const sendUpdateWrapper = sendUpdateMatch[2] && sendUpdateMatch[2][2] && (sendUpdateMatch[2][2][14] || sendUpdateMatch[2][2][2]);
          const sendUpdate = sendUpdateWrapper[1];
          sendUpdate[9][2][0][2] = newBody;
          return JSON.stringify(parsed);
        }
        function parseComposeRequestBody(request) {
          const requestParsed = JSON.parse(request);
          try {
            if (Array.isArray(requestParsed)) {
              const parsed = parseComposeRequestBody_2022_09_09(requestParsed);
              if (parsed) {
                return {
                  type: parsed.type,
                  to: parsed.to,
                  cc: parsed.cc,
                  bcc: parsed.bcc,
                  draftID: parsed.messageId.replace("msg-a:", ""),
                  subject: parsed.subject,
                  body: parsed.body
                };
              }
              return null;
            }
          } catch (err) {
            injected_logger.eventSdkPassive("connection.requestResponseParsingFailed", {
              requestParseError: err
            });
          }
          return getDetailsOfComposeRequest(requestParsed);
        }
        function parseComposeResponseBody(response) {
          const responseParsed = JSON.parse(response);
          if (Array.isArray(responseParsed)) {
            return parseComposeResponseBody_2022_09_09(responseParsed);
          }
          return [];
        }
        function replaceBodyContentInComposeSendRequestBody(request, newBodyHtmlContent) {
          const requestParsed = JSON.parse(request);
          try {
            if (Array.isArray(requestParsed)) {
              const replacedRequestObj = replaceBodyContentInComposeSendRequestBody_2022_09_09(requestParsed, newBodyHtmlContent);
              if (replacedRequestObj) {
                return JSON.stringify(replacedRequestObj);
              }
              return request;
            }
          } catch (err) {
            injected_logger.eventSdkPassive("connection.requestResponseParsingFailed", {
              replaceBodyFailed: err
            });
          }
          return replaceEmailBodyForSendRequest(request, newBodyHtmlContent);
        }
        function logErrorExceptEventListeners(err, details) {
          if (details !== "XMLHttpRequest event listener error") {
            injected_logger.error(err, details);
          } else {
            setTimeout(function() {
              throw err;
            }, 1);
          }
        }
        function setupGmailInterceptor() {
          let jsFrame = null;
          const js_frame_element = top.document.getElementById("js_frame");
          if (js_frame_element) {
            jsFrame = js_frame_element.contentDocument.defaultView;
          } else {
            injected_logger.eventSdkPassive("noJSFrameElementFound");
          }
          setupGmailInterceptorOnFrames(window, jsFrame);
        }
        function setupGmailInterceptorOnFrames(mainFrame, jsFrame) {
          const main_wrappers = [], js_frame_wrappers = [];
          {
            const main_originalXHR = mainFrame.XMLHttpRequest;
            mainFrame.XMLHttpRequest = XHRProxyFactory(main_originalXHR, main_wrappers, {
              logError: logErrorExceptEventListeners
            });
          }
          if (jsFrame) {
            const js_frame_originalXHR = jsFrame.XMLHttpRequest;
            jsFrame.XMLHttpRequest = XHRProxyFactory(js_frame_originalXHR, js_frame_wrappers, {
              logError: logErrorExceptEventListeners
            });
          }
          setup();
          message_metadata_holder_setup();
          {
            const modifiers = {};
            kefir_esm["default"].fromEvents(document, "inboxSDKregisterComposeRequestModifier").onValue((_ref) => {
              let {
                detail
              } = _ref;
              const keyId = detail.composeid || detail.draftID;
              if (!modifiers[keyId]) {
                modifiers[keyId] = [];
              }
              modifiers[keyId].push(detail.modifierId);
            });
            kefir_esm["default"].fromEvents(document, "inboxSDKunregisterComposeRequestModifier").onValue((_ref2) => {
              let {
                detail
              } = _ref2;
              const {
                keyId,
                modifierId
              } = detail;
              modifiers[keyId] = modifiers[keyId].filter((item) => item !== modifierId);
              if (modifiers[keyId].length === 0) {
                delete modifiers[keyId];
              }
            });
            js_frame_wrappers.push({
              isRelevantTo: function(connection) {
                return connection.params.act === "sm";
              },
              originalSendBodyLogger: function(connection, body) {
                triggerEvent({
                  type: "emailSending",
                  body
                });
              },
              requestChanger: async function(connection, request) {
                let composeParams = querystring_es3.parse(request.body);
                const composeid = composeParams.composeid;
                const composeModifierIds = modifiers[composeParams.composeid];
                if (!composeModifierIds || composeModifierIds.length === 0) {
                  return request;
                }
                for (let ii = 0;ii < composeModifierIds.length; ii++) {
                  const modifierId = composeModifierIds[ii];
                  const modificationPromise = kefir_esm["default"].fromEvents(document, "inboxSDKcomposeRequestModified").filter((_ref3) => {
                    let {
                      detail
                    } = _ref3;
                    return detail.composeid === composeid && detail.modifierId === modifierId;
                  }).take(1).map((_ref4) => {
                    let {
                      detail
                    } = _ref4;
                    return detail.composeParams;
                  }).toPromise();
                  triggerEvent({
                    type: "inboxSDKmodifyComposeRequest",
                    composeid,
                    modifierId,
                    composeParams: {
                      body: composeParams.body,
                      isPlainText: composeParams.ishtml !== "1"
                    }
                  });
                  const newComposeParams = await modificationPromise;
                  composeParams = Object.assign({}, composeParams, newComposeParams);
                }
                return Object.assign({}, request, {
                  body: stringifyComposeParams(composeParams)
                });
              },
              afterListeners: function(connection) {
                if (connection.status === 200) {
                  triggerEvent({
                    type: "emailSent",
                    responseText: connection.originalResponseText,
                    originalSendBody: connection.originalSendBody
                  });
                  if (connection.originalSendBody) {
                    const composeParams = querystring_es3.parse(connection.originalSendBody);
                    delete modifiers[composeParams.composeid];
                  }
                }
              }
            });
            js_frame_wrappers.push({
              isRelevantTo: function(connection) {
                return connection.params.act === "sd";
              },
              originalSendBodyLogger: function(connection, body) {
                triggerEvent({
                  type: "emailDraftSaveSending",
                  body
                });
              },
              afterListeners: function(connection) {
                if (connection.status === 200) {
                  triggerEvent({
                    type: "emailDraftReceived",
                    responseText: connection.originalResponseText,
                    originalSendBody: connection.originalSendBody,
                    connectionDetails: {
                      method: connection.method,
                      url: connection.url,
                      params: connection.params,
                      responseType: connection.responseType
                    }
                  });
                }
              }
            });
            {
              const currentSendConnectionIDs = new WeakMap;
              const currentDraftSaveConnectionIDs = new WeakMap;
              const currentFirstDraftSaveConnectionIDs = new WeakMap;
              main_wrappers.push({
                isRelevantTo(connection) {
                  return /sync(?:\/u\/\d+)?\/i\/s/.test(connection.url);
                },
                originalSendBodyLogger(connection) {
                  if (connection.originalSendBody) {
                    const composeRequestDetails = parseComposeRequestBody(connection.originalSendBody);
                    if (!composeRequestDetails) {
                      return;
                    }
                    const {
                      draftID
                    } = composeRequestDetails;
                    switch (composeRequestDetails.type) {
                      case "FIRST_DRAFT_SAVE":
                        currentFirstDraftSaveConnectionIDs.set(connection, draftID);
                        break;
                      case "DRAFT_SAVE":
                        currentDraftSaveConnectionIDs.set(connection, draftID);
                        break;
                      case "SEND":
                        currentSendConnectionIDs.set(connection, draftID);
                        triggerEvent({
                          type: "emailSending",
                          draftID
                        });
                        break;
                    }
                  }
                },
                requestChanger: async function(connection, request) {
                  const composeRequestDetails = parseComposeRequestBody(request.body);
                  if (!composeRequestDetails || composeRequestDetails.type !== "SEND")
                    return request;
                  const {
                    draftID
                  } = composeRequestDetails;
                  const composeModifierIds = modifiers[draftID];
                  if (!composeModifierIds || composeModifierIds.length === 0)
                    return request;
                  let newEmailBody = composeRequestDetails.body;
                  for (let ii = 0;ii < composeModifierIds.length; ii++) {
                    const modifierId = composeModifierIds[ii];
                    const modificationPromise = kefir_esm["default"].fromEvents(document, "inboxSDKcomposeRequestModified").filter((_ref5) => {
                      let {
                        detail
                      } = _ref5;
                      return detail.draftID === draftID && detail.modifierId === modifierId;
                    }).take(1).map((_ref6) => {
                      let {
                        detail
                      } = _ref6;
                      return detail.composeParams;
                    }).toPromise();
                    triggerEvent({
                      type: "inboxSDKmodifyComposeRequest",
                      draftID,
                      modifierId,
                      composeParams: {
                        body: newEmailBody,
                        isPlainText: false
                      }
                    });
                    const newComposeParams = await modificationPromise;
                    newEmailBody = newComposeParams.body;
                  }
                  return Object.assign({}, request, {
                    body: replaceBodyContentInComposeSendRequestBody(request.body, newEmailBody)
                  });
                },
                afterListeners(connection) {
                  if (currentSendConnectionIDs.has(connection) || currentDraftSaveConnectionIDs.has(connection) || currentFirstDraftSaveConnectionIDs.has(connection)) {
                    const sendFailed = () => {
                      triggerEvent({
                        type: "emailSendFailed",
                        draftID
                      });
                      currentSendConnectionIDs.delete(connection);
                    };
                    const draftID = currentSendConnectionIDs.get(connection) || currentDraftSaveConnectionIDs.get(connection) || currentFirstDraftSaveConnectionIDs.get(connection);
                    if (connection.status !== 200 || !connection.originalResponseText) {
                      sendFailed();
                      return;
                    }
                    try {
                      const responsesParsed = parseComposeResponseBody(connection.originalResponseText);
                      for (const responseParsed of responsesParsed) {
                        if (draftID && !responseParsed.messageId.endsWith(draftID)) {
                          continue;
                        }
                        if (responseParsed.type === "FIRST_DRAFT_SAVE" || responseParsed.type === "DRAFT_SAVE") {
                          triggerEvent({
                            draftID,
                            type: "emailDraftReceived",
                            rfcID: responseParsed.rfcID,
                            threadID: responseParsed.threadId,
                            messageID: responseParsed.messageId,
                            oldMessageID: responseParsed.oldMessageId,
                            oldThreadID: responseParsed.oldThreadId
                          });
                          currentSendConnectionIDs.delete(connection);
                          currentDraftSaveConnectionIDs.delete(connection);
                          currentFirstDraftSaveConnectionIDs.delete(connection);
                          return;
                        } else if (responseParsed.type === "SEND") {
                          triggerEvent({
                            draftID,
                            type: "emailSent",
                            rfcID: responseParsed.rfcID,
                            threadID: responseParsed.threadId,
                            messageID: responseParsed.messageId,
                            oldMessageID: responseParsed.oldMessageId,
                            oldThreadID: responseParsed.oldThreadId
                          });
                          currentSendConnectionIDs.delete(connection);
                          currentDraftSaveConnectionIDs.delete(connection);
                          currentFirstDraftSaveConnectionIDs.delete(connection);
                          return;
                        }
                      }
                    } catch (err) {
                      injected_logger.eventSdkPassive("connection.requestResponseParsingFailed", {
                        responseParseError: err
                      });
                    }
                    const originalResponse = JSON.parse(connection.originalResponseText);
                    if (currentFirstDraftSaveConnectionIDs.has(connection)) {
                      const wrapper = originalResponse[2] && originalResponse[2][6] && originalResponse[2][6][0] && originalResponse[2][6][0][1];
                      if (wrapper) {
                        const threadUpdate = wrapper[3] && wrapper[3][7] && wrapper[3][7][1];
                        const messageUpdate = threadUpdate && threadUpdate[5] && threadUpdate[5][0];
                        if (threadUpdate && messageUpdate) {
                          triggerEvent({
                            draftID,
                            type: "emailDraftReceived",
                            rfcID: messageUpdate[14],
                            threadID: threadUpdate[4].split("|")[0],
                            messageID: messageUpdate[1],
                            oldMessageID: messageUpdate[56],
                            oldThreadID: threadUpdate[20]
                          });
                        } else {
                          injected_logger.error(new Error("Could not parse draft save"));
                        }
                      } else {
                        injected_logger.eventSdkPassive("old compose draft id handling hit");
                        const oldWrapper = originalResponse[2] && originalResponse[2][6] && originalResponse[2][6][1] && originalResponse[2][6][1][1];
                        if (oldWrapper) {
                          const saveUpdate = oldWrapper[3] && oldWrapper[3][1] && oldWrapper[3][1][1];
                          if (saveUpdate) {
                            triggerEvent({
                              draftID,
                              type: "emailDraftReceived",
                              rfcID: saveUpdate[14],
                              messageID: saveUpdate[1],
                              oldMessageID: saveUpdate[48] ? new (bignumber_default())(saveUpdate[48]).toString(16) : saveUpdate[56],
                              syncThreadID: oldWrapper[1]
                            });
                          }
                        }
                      }
                    } else {
                      const updateList = originalResponse[2]?.[6];
                      if (!updateList) {
                        sendFailed();
                        return;
                      }
                      const sendUpdateMatch = updateList.find((update) => update[1]?.[3]?.[7]?.[1]?.[5]?.[0]?.[14] && update[1][3][7][1][5].find((message) => includes_default()(message[1], draftID)));
                      if (!sendUpdateMatch) {
                        if (currentSendConnectionIDs.has(connection)) {
                          const minimalSendUpdates = updateList.filter((update) => update[1]?.[3]?.[5]?.[3]);
                          if (minimalSendUpdates.length > 0) {
                            const threadID2 = minimalSendUpdates[0][1][1] ? minimalSendUpdates[0][1][1].replace(/\|.*$/, "") : undefined;
                            triggerEvent({
                              draftID,
                              type: "emailSent",
                              threadID: threadID2,
                              messageID: minimalSendUpdates[0][1][3]?.[5]?.[5]?.[0] || minimalSendUpdates[0][1][3][5][3]?.[0]
                            });
                          } else {
                            sendFailed();
                          }
                        } else {
                          sendFailed();
                        }
                        return;
                      }
                      const sendUpdateWrapper = sendUpdateMatch[1]?.[3]?.[7]?.[1];
                      const sendUpdate = sendUpdateWrapper[5].find((message) => message[1].includes(draftID));
                      if (!sendUpdate) {
                        sendFailed();
                        return;
                      }
                      const isEmailSentResponse = currentSendConnectionIDs.has(connection);
                      if (!Array.isArray(sendUpdate[11])) {
                        injected_logger.error(new Error("sendUpdate[11] was not an array"));
                      } else {
                        if (isEmailSentResponse) {
                          if (sendUpdate[11].indexOf("^r") >= 0) {
                            injected_logger.error(new Error('sendUpdate[11] unexpectedly contained "^r"'));
                          }
                        }
                      }
                      if (isEmailSentResponse) {
                        if (sendUpdate[22] !== undefined && sendUpdate[22] !== 3) {
                          injected_logger.error(new Error("sendUpdate[22] was not expected value"), {
                            value: sendUpdate[22]
                          });
                        }
                      }
                      const threadID = sendUpdateWrapper[4] ? sendUpdateWrapper[4].replace(/\|.*$/, "") : undefined;
                      triggerEvent({
                        draftID,
                        type: isEmailSentResponse ? "emailSent" : "emailDraftReceived",
                        rfcID: sendUpdate[14],
                        messageID: sendUpdate[1],
                        oldMessageID: sendUpdate[48] ? new (bignumber_default())(sendUpdate[48]).toString(16) : sendUpdate[56],
                        threadID,
                        oldThreadID: sendUpdateWrapper[18] != null ? new (bignumber_default())(sendUpdateWrapper[18]).toString(16) : sendUpdateWrapper[20]
                      });
                    }
                    currentSendConnectionIDs.delete(connection);
                    currentDraftSaveConnectionIDs.delete(connection);
                    currentFirstDraftSaveConnectionIDs.delete(connection);
                  }
                }
              });
            }
          }
          {
            js_frame_wrappers.push({
              isRelevantTo(connection) {
                return !!connection.params.search && connection.params.view === "tl";
              },
              async responseTextChanger(connection, responseText) {
                return responseText;
              },
              originalResponseTextLogger(connection) {
                if (connection.status === 200) {
                  const responseText = connection.originalResponseText;
                  processThreadListResponse(responseText);
                }
              }
            });
          }
          {
            {
              js_frame_wrappers.push({
                isRelevantTo(connection) {
                  return connection.params.view === "cv";
                },
                originalResponseTextLogger(connection) {
                  if (connection.status === 200) {
                    const groupedMessages = gmail_response_processor.St(connection.originalResponseText);
                    add(groupedMessages);
                  }
                }
              });
            }
            {
              main_wrappers.push({
                isRelevantTo: function(connection) {
                  return /sync(?:\/u\/\d+)?\/i\/bv/.test(connection.url);
                },
                originalResponseTextLogger(connection) {
                  if (connection.status === 200) {
                    const threads = extractThreadsFromSearchResponse(connection.originalResponseText);
                    add(threads.map((syncThread) => ({
                      syncThreadID: syncThread.syncThreadID,
                      threadID: syncThread.oldGmailThreadID,
                      messages: syncThread.extraMetaData.syncMessageData.map((syncMessage) => ({
                        date: syncMessage.date,
                        recipients: syncMessage.recipients
                      }))
                    })));
                  }
                }
              });
              main_wrappers.push({
                isRelevantTo: function(connection) {
                  return /sync(?:\/u\/\d+)?\/i\/fd/.test(connection.url);
                },
                originalResponseTextLogger(connection) {
                  if (connection.status === 200) {
                    const threads = extractThreadsFromThreadResponse(connection.originalResponseText);
                    add(threads.map((syncThread) => ({
                      syncThreadID: syncThread.syncThreadID,
                      threadID: syncThread.oldGmailThreadID,
                      messages: syncThread.extraMetaData.syncMessageData.map((syncMessage) => ({
                        date: syncMessage.date,
                        recipients: syncMessage.recipients
                      }))
                    })));
                  }
                }
              });
            }
          }
          {
            const providers = Object.create(null);
            let currentQuery;
            let suggestionModifications;
            let currentQueryDefer;
            document.addEventListener("inboxSDKregisterSuggestionsModifier", function(_ref7) {
              let {
                detail
              } = _ref7;
              providers[detail.providerID] = {
                position: Object.keys(providers).length
              };
            });
            document.addEventListener("inboxSDKprovideSuggestions", function(_ref8) {
              let {
                detail
              } = _ref8;
              if (detail.query === currentQuery) {
                const provider = providers[detail.providerID];
                if (!provider) {
                  throw new Error("provider does not exist for providerID");
                }
                if (suggestionModifications == null) {
                  throw new Error("tried to modified a null suggestionModifications");
                }
                suggestionModifications[provider.position] = detail.suggestions;
                if (suggestionModifications.filter(Boolean).length === Object.keys(providers).length) {
                  if (currentQueryDefer == null) {
                    throw new Error("tried to resolve a null currentQueryDefer");
                  }
                  currentQueryDefer.resolve(flatten_default()(suggestionModifications));
                  currentQueryDefer = currentQuery = suggestionModifications = null;
                }
              }
            });
            main_wrappers.push({
              isRelevantTo(connection) {
                return Object.keys(providers).length > 0 && !!connection.url.match(/^\/cloudsearch\/request\?/) && connection.params.client == "gmail" && connection.params.gs_ri == "gmail";
              },
              originalSendBodyLogger(connection, body) {
                const parsedBody = querystring_es3.parse(body);
                if (!parsedBody.request) {
                  return;
                }
                const query = JSON.parse(parsedBody.request)[2];
                if (!query) {
                  return;
                }
                currentQuery = query;
                if (currentQueryDefer)
                  currentQueryDefer.resolve();
                currentQueryDefer = connection._defer = defer();
                suggestionModifications = [];
                triggerEvent({
                  type: "suggestionsRequest",
                  query: currentQuery
                });
              },
              async responseTextChanger(connection, responseText) {
                if (connection._defer && connection.status === 200) {
                  const modifications = await connection._defer.promise;
                  if (modifications) {
                    let modified;
                    try {
                      modified = (0, modify_suggestions.A)(responseText, modifications);
                    } catch (e) {
                      injected_logger.eventSdkPassive("suggestionsModified.error", {
                        query: currentQuery,
                        originalResponseText: responseText,
                        error: e instanceof Error && e.message
                      }, true);
                      throw e;
                    }
                    return modified;
                  }
                }
                return responseText;
              }
            });
          }
          {
            const customSearchTerms = [];
            let queryReplacement;
            document.addEventListener("inboxSDKcreateCustomSearchTerm", function(event) {
              customSearchTerms.push(event.detail.term);
            });
            document.addEventListener("inboxSDKsearchReplacementReady", function(event) {
              if (queryReplacement.query === event.detail.query) {
                queryReplacement.newQuery.resolve(event.detail.newQuery);
              }
            });
            js_frame_wrappers.push({
              isRelevantTo: function(connection) {
                let customSearchTerm;
                const params = connection.params;
                if (connection.method === "POST" && params.search && params.view === "tl" && connection.url.match(/^\?/) && params.q && (customSearchTerm = intersection_default()(customSearchTerms, quotedSplit(params.q))[0])) {
                  if (queryReplacement && queryReplacement.query === params.q && queryReplacement.start != params.start) {
                    connection._queryReplacement = queryReplacement;
                    queryReplacement.start = params.start;
                  } else {
                    if (queryReplacement) {
                      queryReplacement.newQuery.resolve(queryReplacement.query);
                    }
                    queryReplacement = connection._queryReplacement = {
                      term: customSearchTerm,
                      query: params.q,
                      start: params.start,
                      newQuery: defer()
                    };
                    triggerEvent({
                      type: "searchQueryForReplacement",
                      term: customSearchTerm,
                      query: params.q
                    });
                  }
                  return true;
                }
                return false;
              },
              requestChanger: function(connection, request) {
                return connection._queryReplacement.newQuery.promise.then(function(newQuery) {
                  const newParams = clone_default()(connection.params);
                  newParams.q = newQuery;
                  return {
                    method: request.method,
                    url: "?" + (0, querystring_es3.stringify)(newParams),
                    body: request.body
                  };
                });
              }
            });
            main_wrappers.push({
              isRelevantTo: function(connection) {
                return connection.method === "POST" && /sync(?:\/u\/\d+)?\/i\/bv/.test(connection.url);
              },
              requestChanger: function(connection, request) {
                let customSearchTerm;
                const body = JSON.parse(request.body);
                let newFormat = false;
                let payload, searchString, pageOffset;
                if (Array.isArray(body)) {
                  newFormat = true;
                  payload = body[0];
                  searchString = payload[3];
                  pageOffset = payload[9];
                } else {
                  payload = body[1];
                  searchString = payload[4];
                  pageOffset = payload[10];
                }
                const isSyncAPISearchWithCustomTerm = payload[newFormat ? 0 : 1] === 79 && typeof searchString === "string" && (customSearchTerm = intersection_default()(customSearchTerms, quotedSplit(searchString))[0]);
                if (!isSyncAPISearchWithCustomTerm)
                  return Promise.resolve(request);
                if (queryReplacement && queryReplacement.query === searchString && queryReplacement.start != pageOffset) {
                  connection._queryReplacement = queryReplacement;
                  queryReplacement.start = pageOffset;
                } else {
                  if (queryReplacement) {
                    queryReplacement.newQuery.resolve(queryReplacement.query);
                  }
                  queryReplacement = connection._queryReplacement = {
                    term: customSearchTerm,
                    query: searchString,
                    start: pageOffset,
                    newQuery: defer()
                  };
                  triggerEvent({
                    type: "searchQueryForReplacement",
                    term: customSearchTerm,
                    query: searchString
                  });
                }
                return connection._queryReplacement.newQuery.promise.then(function(newQuery) {
                  if (newFormat) {
                    body[0][3] = newQuery;
                  } else {
                    body[1][4] = newQuery;
                  }
                  return {
                    method: request.method,
                    url: request.url,
                    body: JSON.stringify(body)
                  };
                });
              }
            });
          }
          {
            const customSearchQueries = [];
            let customListJob;
            document.addEventListener("inboxSDKcustomListRegisterQuery", (event) => {
              customSearchQueries.push(event.detail.query);
            });
            document.addEventListener("inboxSDKcustomListNewQuery", (event) => {
              if (customListJob.query === event.detail.query && customListJob.start === event.detail.start) {
                const {
                  newQuery,
                  newStart
                } = event.detail;
                customListJob.newRequestParams.resolve({
                  query: newQuery,
                  start: newStart
                });
              }
            });
            document.addEventListener("inboxSDKcustomListResults", (event) => {
              if (customListJob.query === event.detail.query) {
                customListJob.newResults.resolve(event.detail.newResults);
              }
            });
            js_frame_wrappers.push({
              isRelevantTo: function(connection) {
                const params = connection.params;
                if (connection.method === "POST" && params.search && params.view === "tl" && connection.url.match(/^\?/) && params.q && !params.act && find_default()(customSearchQueries, (x) => x === params.q)) {
                  if (customListJob) {
                    customListJob.newRequestParams.resolve({
                      query: customListJob.query,
                      start: customListJob.start
                    });
                    customListJob.newResults.resolve(null);
                  }
                  customListJob = connection._customListJob = {
                    query: params.q,
                    start: +params.start,
                    newRequestParams: defer(),
                    newResults: defer()
                  };
                  triggerEvent({
                    type: "searchForReplacement",
                    query: customListJob.query,
                    start: customListJob.start
                  });
                  return true;
                }
                return false;
              },
              requestChanger: function(connection, request) {
                return connection._customListJob.newRequestParams.promise.then((_ref9) => {
                  let {
                    query,
                    start
                  } = _ref9;
                  const newParams = clone_default()(connection.params);
                  newParams.q = query;
                  newParams.start = start;
                  return {
                    method: request.method,
                    url: "?" + (0, querystring_es3.stringify)(newParams),
                    body: request.body
                  };
                });
              },
              responseTextChanger: function(connection, response) {
                triggerEvent({
                  type: "searchResultsResponse",
                  query: connection._customListJob.query,
                  start: connection._customListJob.start,
                  response
                });
                return connection._customListJob.newResults.promise.then((newResults) => newResults === null ? response : newResults);
              }
            });
            main_wrappers.push({
              isRelevantTo: function(connection) {
                if (/sync(?:\/u\/\d+)?\/i\/bv/.test(connection.url)) {
                  if (customListJob) {
                    customListJob.newRequestParams.resolve({
                      query: customListJob.query,
                      start: customListJob.start
                    });
                    customListJob.newResults.resolve(null);
                  }
                  return true;
                }
                return false;
              },
              requestChanger: async function(connection, request) {
                if (request.body) {
                  const parsedBody = JSON.parse(request.body);
                  const newFormat = Array.isArray(parsedBody);
                  const searchQuery = (newFormat ? parsedBody && parsedBody[0] && parsedBody[0][3] : parsedBody && parsedBody[1] && parsedBody[1][4]) || "";
                  if (find_default()(customSearchQueries, (x) => x === searchQuery)) {
                    customListJob = connection._customListJob = {
                      query: searchQuery,
                      start: newFormat ? parsedBody[0][9] : parsedBody[1][10],
                      newRequestParams: defer(),
                      newResults: defer()
                    };
                    triggerEvent({
                      type: "searchForReplacement",
                      query: customListJob.query,
                      start: customListJob.start
                    });
                    return connection._customListJob.newRequestParams.promise.then((_ref10) => {
                      let {
                        query,
                        start
                      } = _ref10;
                      if (newFormat) {
                        parsedBody[0][3] = query;
                        parsedBody[0][9] = start;
                      } else {
                        parsedBody[1][4] = query;
                        parsedBody[1][10] = start;
                      }
                      return {
                        method: request.method,
                        url: request.url,
                        body: JSON.stringify(parsedBody)
                      };
                    });
                  }
                }
                return request;
              },
              responseTextChanger: async function(connection, response) {
                if (connection._customListJob) {
                  triggerEvent({
                    type: "searchResultsResponse",
                    query: connection._customListJob.query,
                    start: connection._customListJob.start,
                    response
                  });
                  return connection._customListJob.newResults.promise.then((newResults) => newResults === null ? response : newResults);
                } else {
                  return response;
                }
              }
            });
          }
          {
            const saveBTAIHeader = (header) => {
              document.head.setAttribute("data-inboxsdk-btai-header", header);
              triggerEvent({
                type: "btaiHeaderReceived"
              });
            };
            main_wrappers.push({
              isRelevantTo(connection) {
                return /sync(?:\/u\/\d+)?\//.test(connection.url) && !document.head.hasAttribute("data-inboxsdk-btai-header");
              },
              originalSendBodyLogger(connection) {
                if (connection.headers["X-Gmail-BTAI"]) {
                  saveBTAIHeader(connection.headers["X-Gmail-BTAI"]);
                }
              }
            });
            const saveXsrfTokenHeader = (header) => {
              document.head.setAttribute("data-inboxsdk-xsrf-token", header);
              triggerEvent({
                type: "xsrfTokenHeaderReceived"
              });
            };
            main_wrappers.push({
              isRelevantTo(connection) {
                return /sync(?:\/u\/\d+)?\//.test(connection.url) && !document.head.hasAttribute("data-inboxsdk-xsrf-token");
              },
              originalSendBodyLogger(connection) {
                if (connection.headers["X-Framework-Xsrf-Token"]) {
                  saveXsrfTokenHeader(connection.headers["X-Framework-Xsrf-Token"]);
                }
              }
            });
          }
          {
            let googleApiKey = "AIzaSyBm7aDMG9actsWSlx-MvrYsepwdnLgz69I";
            document.addEventListener("inboxSDKgetGoogleRequestHeaders", () => {
              const authorizationHeader = window.gapi.auth.getAuthHeaderValueForFirstParty([]);
              const headers = {
                authorization: authorizationHeader,
                "x-goog-api-key": googleApiKey
              };
              document.head.setAttribute("data-inboxsdk-google-headers", JSON.stringify(headers));
            });
            main_wrappers.push({
              isRelevantTo(connection) {
                if (connection.url.startsWith("https://")) {
                  const url = new URL(connection.url);
                  return url.hostname.endsWith(".google.com");
                }
                return false;
              },
              originalSendBodyLogger(connection) {
                if (connection.headers["X-Goog-Api-Key"]) {
                  googleApiKey = connection.headers["X-Goog-Api-Key"];
                }
              }
            });
          }
        }
        function triggerEvent(detail) {
          document.dispatchEvent(new CustomEvent("inboxSDKajaxIntercept", {
            bubbles: true,
            cancelable: false,
            detail
          }));
        }
        function stringifyComposeParams(inComposeParams) {
          const composeParams = clone_default()(inComposeParams);
          const string = `=${stringifyComposeRecipientParam(composeParams.to, "to")}&=${stringifyComposeRecipientParam(composeParams.cc, "cc")}&=${stringifyComposeRecipientParam(composeParams.bcc, "bcc")}`;
          delete composeParams.to;
          delete composeParams.bcc;
          delete composeParams.cc;
          return string + "&" + querystring_es3.stringify(composeParams);
        }
        function stringifyComposeRecipientParam(value, paramType) {
          let string = "";
          if (Array.isArray(value)) {
            for (let ii = 0;ii < value.length; ii++) {
              string += `&${paramType}=${encodeURIComponent(value[ii])}`;
            }
          } else {
            string += `&${paramType}=${encodeURIComponent(value)}`;
          }
          return string;
        }
      },
      8809: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => setupGmonkeyHandler
        });
        function setupGmonkeyHandler() {
          const gmonkeyPromise = setupGmonkey();
          document.addEventListener("inboxSDKtellMeIsConversationViewDisabled", function() {
            gmonkeyPromise.then((gmonkey) => {
              const answer = gmonkey.isConversationViewDisabled();
              const event = document.createEvent("CustomEvent");
              event.initCustomEvent("inboxSDKgmonkeyResponse", false, false, answer);
              document.dispatchEvent(event);
            });
          });
          document.addEventListener("inboxSDKtellMeCurrentThreadId", function(event) {
            let threadId;
            if (event.detail.isPreviewedThread) {
              const rows = Array.from(document.querySelectorAll("[gh=tl] tr.aps"));
              if (rows.length > 0) {
                const elementWithId = rows.map((row) => row.querySelector("[data-thread-id]")).filter(Boolean)[0];
                if (elementWithId) {
                  threadId = elementWithId.getAttribute("data-thread-id");
                } else {
                  threadId = rows[0].getAttribute("data-inboxsdk-threadid");
                }
              }
            } else {
              threadId = window.gmonkey?.v2?.getCurrentThread?.()?.getThreadId();
            }
            if (threadId) {
              threadId = threadId.replace("#", "");
              event.target.setAttribute("data-inboxsdk-currentthreadid", threadId);
            }
          });
        }
        function setupGmonkey() {
          return new Promise((resolve) => {
            function check() {
              if (!window.gmonkey) {
                setTimeout(check, 500);
              } else {
                window.gmonkey.load("2.0", resolve);
              }
            }
            check();
          });
        }
      },
      4530: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.r(__webpack_exports__2);
        __webpack_require__2.d(__webpack_exports__2, {
          error: () => error,
          eventSdkPassive: () => eventSdkPassive
        });
        function error(err, details) {
          if (!err) {
            err = new Error("No error given");
          }
          console.error("Error in injected script", err, details);
          try {
            JSON.stringify(details);
          } catch (e) {
            details = "<failed to jsonify>";
          }
          const errorProperties = {};
          for (const name in err) {
            if (Object.prototype.hasOwnProperty.call(err, name)) {
              try {
                const value = err[name];
                JSON.stringify(value);
                errorProperties[name] = value;
              } catch (err2) {}
            }
          }
          if (Object.keys(errorProperties).length > 0) {
            details = {
              errorProperties,
              details
            };
          }
          document.dispatchEvent(new CustomEvent("inboxSDKinjectedError", {
            bubbles: false,
            cancelable: false,
            detail: {
              message: err && err.message,
              stack: err && err.stack,
              details
            }
          }));
        }
        function eventSdkPassive(name, details, sensitive) {
          try {
            JSON.stringify(details);
          } catch (e) {
            details = "<failed to jsonify>";
          }
          document.dispatchEvent(new CustomEvent("inboxSDKinjectedEventSdkPassive", {
            bubbles: false,
            cancelable: false,
            detail: {
              name,
              details,
              sensitive
            }
          }));
        }
      },
      6465: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => setupDataExposer
        });
        var find = __webpack_require__2(4455);
        var find_default = /* @__PURE__ */ __webpack_require__2.n(find);
        var injected_logger = __webpack_require__2(4530);

        class WaitForError extends Error {
          name = "WaitForError";
          constructor() {
            super("waitFor timeout");
          }
        }
        function waitFor(condition) {
          let timeout = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : 120 * 1000;
          let steptime = arguments.length > 2 && arguments[2] !== undefined ? arguments[2] : 250;
          const timeoutError = new WaitForError;
          return new Promise(function(resolve, reject) {
            let waited = 0;
            function step() {
              try {
                const result = condition();
                if (result) {
                  resolve(result);
                } else {
                  if (waited >= timeout) {
                    reject(timeoutError);
                  } else {
                    waited += steptime;
                    setTimeout(step, steptime);
                  }
                }
              } catch (e) {
                reject(e);
              }
            }
            setTimeout(step, 1);
          });
        }
        function stupidToBool(stupid) {
          switch ("" + stupid) {
            case "1":
            case "t":
            case "true":
              return true;
            default:
              return false;
          }
        }
        function getSettingValue(settings, name) {
          var entry = find_default()(settings, (setting) => setting[0] === name);
          return entry ? stupidToBool(entry[1]) : false;
        }
        function getContext() {
          let context = __webpack_require__2.g;
          try {
            if (context.GLOBALS)
              return context;
            if (__webpack_require__2.g.opener && __webpack_require__2.g.opener.top) {
              __webpack_require__2.g.opener.top.location.href;
              context = __webpack_require__2.g.opener.top;
            }
          } catch (err) {
            context = __webpack_require__2.g;
          }
          return context;
        }
        function setupDataExposer() {
          let context;
          waitFor(() => {
            context = getContext();
            return context && (context.GLOBALS || context.gbar);
          }).then(() => {
            if (!context)
              return;
            var userEmail = context.GLOBALS ? context.GLOBALS[10] : context.gbar._CONFIG[0][10][5];
            document.head.setAttribute("data-inboxsdk-user-email-address", userEmail);
            var userLanguage = context.GLOBALS ? context.GLOBALS[4].split(".")[1] : context.gbar._CONFIG[0][0][4];
            document.head.setAttribute("data-inboxsdk-user-language", userLanguage);
            document.head.setAttribute("data-inboxsdk-using-sync-api", context.GM_SPT_ENABLED);
            if (context.GLOBALS) {
              document.head.setAttribute("data-inboxsdk-ik-value", context.GLOBALS[9]);
              document.head.setAttribute("data-inboxsdk-action-token-value", context.GM_ACTION_TOKEN);
              var globalSettingsHolder = find_default()(context.GLOBALS[17], (item) => item[0] === "p");
              if (!globalSettingsHolder) {
                return;
              } else {
                var globalSettings = globalSettingsHolder[1];
                {
                  var previewPaneLabEnabled = getSettingValue(globalSettings, "bx_lab_1252");
                  var previewPaneEnabled = getSettingValue(globalSettings, "bx_spa");
                  var previewPaneVertical = getSettingValue(globalSettings, "bx_spo");
                  var previewPaneMode = previewPaneLabEnabled && previewPaneEnabled ? previewPaneVertical ? "vertical" : "horizontal" : "none";
                  document.head.setAttribute("data-inboxsdk-user-preview-pane-mode", previewPaneMode);
                }
              }
            } else {
              const preloadDataSearchString = "window.BT_EmbeddedAppData=[";
              const preloadScript = find_default()(document.querySelectorAll("script:not([src])"), (script) => script.text && script.text.slice(0, 500).indexOf(preloadDataSearchString) > -1);
              if (!preloadScript) {
                injected_logger.error(new Error("Could not read preloaded BT_EmbeddedAppData"));
              } else {
                const {
                  text
                } = preloadScript;
                const firstBracket = text.indexOf("window.BT_EmbeddedAppData=[");
                let lastBracket = text.indexOf(`]
;`, firstBracket);
                if (lastBracket === -1) {
                  lastBracket = text.indexOf("];", firstBracket);
                }
                const preloadData = JSON.parse(text.slice(firstBracket + preloadDataSearchString.length - 1, lastBracket + 1));
                const ikValue = preloadData[11];
                if (typeof ikValue !== "string") {
                  injected_logger.error(new Error("Could not find valid ikValue"));
                } else {
                  document.head.setAttribute("data-inboxsdk-ik-value", ikValue);
                }
                const xsrfToken = preloadData[12];
                if (typeof xsrfToken !== "string") {
                  injected_logger.error(new Error("Could not find valid xsrfToken"));
                } else {
                  document.head.setAttribute("data-inboxsdk-xsrf-token", xsrfToken);
                }
              }
            }
          }).catch((err) => {
            function getStatus() {
              return {
                hasGLOBALS: !!context.GLOBALS,
                hasGbar: !!context.gbar
              };
            }
            var startStatus = getStatus();
            var waitTime = 180 * 1000;
            setTimeout(() => {
              var laterStatus = getStatus();
              injected_logger.eventSdkPassive("waitfor global data", {
                startStatus,
                waitTime,
                laterStatus
              });
            }, waitTime);
            throw err;
          }).catch(injected_logger.error);
        }
      },
      5915: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => setupErrorSilencer
        });
        function setupErrorSilencer() {
          var oldErrorHandlers = [];
          document.addEventListener("inboxSDKsilencePageErrors", function() {
            oldErrorHandlers.push(window.onerror);
            window.onerror = function() {
              if (false) {
                var _len, args, _key;
              }
              return true;
            };
          });
          document.addEventListener("inboxSDKunsilencePageErrors", function() {
            window.onerror = oldErrorHandlers.pop();
          });
        }
      },
      9729: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => setupEventReemitter
        });
        function setupEventReemitter() {
          document.addEventListener("inboxsdk_event_relay", function(event) {
            const newEvent = document.createEvent("Events");
            newEvent.initEvent(event.detail.type, event.detail.bubbles, event.detail.cancelable);
            Object.assign(newEvent, event.detail.props);
            if (event.detail.dataTransfer) {
              const {
                files,
                fileNames
              } = event.detail.dataTransfer;
              if (fileNames) {
                fileNames.forEach((fileName, i) => {
                  const file = files[i];
                  if (typeof file.name !== "string") {
                    file.name = fileName;
                  }
                });
              }
              newEvent.dataTransfer = {
                dropEffect: "none",
                effectAllowed: "all",
                files,
                items: files.map((_ref, i) => {
                  let {
                    type
                  } = _ref;
                  return {
                    kind: "file",
                    type,
                    getAsFile() {
                      return files[i];
                    },
                    getAsString() {
                      throw new Error("getAsString not supported");
                    }
                  };
                }),
                types: ["Files"],
                getData() {
                  return "";
                },
                setData() {},
                setDragImage() {}
              };
            }
            event.target.dispatchEvent(newEvent);
          });
        }
      },
      4630: (module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => setupCustomViewEventAssassin
        });
        var ud__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__2(7332);
        var lodash_includes__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__2(5193);
        var lodash_includes__WEBPACK_IMPORTED_MODULE_0___default = /* @__PURE__ */ __webpack_require__2.n(lodash_includes__WEBPACK_IMPORTED_MODULE_0__);
        var _injected_logger__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__2(4530);
        module = __webpack_require__2.hmd(module);
        function md(value) {
          return {
            value,
            configurable: true
          };
        }
        const blockedAnyModKeys = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", "Enter"];
        const blockedKeyIdentifiers = ["Left", "Right", "Up", "Down"];
        const blockedAnyModCharacters = "!#[]{}_+=-;:\r\n1234567890`~";
        const blockedNoModCharacters = ",xsyemrafz.ujkpnl";
        const blockedShiftCharacters = "parfniut";
        function shouldBlockEvent(event) {
          if (!document.body.classList.contains("inboxsdk__custom_view_active")) {
            return false;
          }
          const target = event.target;
          const key = event.key || String.fromCharCode(event.which || event.keyCode);
          if (event.key === "Escape" && target instanceof HTMLElement && target.closest(".inboxsdk__custom_view")) {
            return true;
          }
          if (lodash_includes__WEBPACK_IMPORTED_MODULE_0___default()(blockedAnyModKeys, key) || lodash_includes__WEBPACK_IMPORTED_MODULE_0___default()(blockedKeyIdentifiers, event.keyIdentifier) || lodash_includes__WEBPACK_IMPORTED_MODULE_0___default()(blockedAnyModCharacters, key) || !event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey && lodash_includes__WEBPACK_IMPORTED_MODULE_0___default()(blockedNoModCharacters, key) || event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey && lodash_includes__WEBPACK_IMPORTED_MODULE_0___default()(blockedShiftCharacters, key.toLowerCase())) {
            if (target instanceof HTMLElement && target.closest("input, textarea, button, [contenteditable]") || target instanceof HTMLElement && !target.closest(".inboxsdk__custom_view") && target.closest("[role=button], [role=link]")) {
              return false;
            }
            return true;
          }
          return false;
        }
        const handler = (0, ud__WEBPACK_IMPORTED_MODULE_1__.defn)(module, function(event) {
          try {
            if (shouldBlockEvent(event)) {
              Object.defineProperties(event, {
                altKey: md(false),
                ctrlKey: md(false),
                shiftKey: md(false),
                metaKey: md(false),
                charCode: md(92),
                code: md("Backslash"),
                key: md("\\"),
                keyCode: md(92),
                which: md(92)
              });
            }
          } catch (err) {
            _injected_logger__WEBPACK_IMPORTED_MODULE_2__.error(err);
          }
        });
        function setupCustomViewEventAssassin() {
          document.addEventListener("keydown", handler, true);
        }
      },
      9234: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => setupInboxCustomViewLinkFixer
        });
        function setupInboxCustomViewLinkFixer() {
          const allowedStartTerms = new Set;
          document.addEventListener("inboxSDKregisterAllowedHashLinkStartTerm", function(event) {
            const term = event.detail.term;
            allowedStartTerms.add(term);
          });
          document.addEventListener("click", function(event) {
            const target = event.target;
            if (!(target instanceof HTMLElement))
              return;
            const anchor = target.closest('a[href^="#"]');
            if (!anchor || !(anchor instanceof HTMLAnchorElement))
              return;
            const m = /^#([^/]+)/.exec(anchor.getAttribute("href") || "");
            if (!m)
              return;
            const startTerm = m[1];
            if (!allowedStartTerms.has(startTerm))
              return;
            event.preventDefault = () => {};
          }, true);
        }
      },
      3095: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => setupPushStateListener
        });
        function setupPushStateListener() {
          const origPushState = history.pushState;
          history.pushState = function() {
            for (var _len = arguments.length, args = new Array(_len), _key = 0;_key < _len; _key++) {
              args[_key] = arguments[_key];
            }
            const ret = origPushState.apply(this, args);
            document.dispatchEvent(new CustomEvent("inboxSDKpushState", {
              bubbles: false,
              cancelable: false,
              detail: {
                args
              }
            }));
            return ret;
          };
        }
      },
      284: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => xhrHelper
        });
        function xhrHelper() {
          document.addEventListener("inboxSDKpageAjax", function(event) {
            const id = event.detail.id;
            const opts = {
              url: event.detail.url,
              method: event.detail.method,
              headers: event.detail.headers,
              xhrFields: event.detail.xhrFields,
              data: event.detail.data
            };
            (async () => {
              const response = await fetch(opts.url, {
                method: opts.method || "GET",
                credentials: "include"
              });
              document.dispatchEvent(new CustomEvent("inboxSDKpageAjaxDone", {
                bubbles: false,
                cancelable: false,
                detail: {
                  id,
                  error: false,
                  text: await response.text(),
                  responseURL: response.url
                }
              }));
            })().catch((err) => {
              document.dispatchEvent(new CustomEvent("inboxSDKpageAjaxDone", {
                bubbles: false,
                cancelable: false,
                detail: {
                  id,
                  error: true,
                  message: err && err.message,
                  stack: err && err.stack,
                  status: err && err.xhr && err.xhr.status
                }
              }));
            });
          });
        }
      },
      1433: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          On: () => cleanupPeopleLine,
          St: () => extractMessages,
          XX: () => deserializeArray,
          eF: () => extractThreadsFromDeserialized,
          iu: () => deserialize,
          lK: () => serialize,
          rq: () => extractThreads
        });
        var lodash_flatten__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__2(4176);
        var lodash_flatten__WEBPACK_IMPORTED_MODULE_0___default = /* @__PURE__ */ __webpack_require__2.n(lodash_flatten__WEBPACK_IMPORTED_MODULE_0__);
        var lodash_last__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__2(6456);
        var lodash_last__WEBPACK_IMPORTED_MODULE_1___default = /* @__PURE__ */ __webpack_require__2.n(lodash_last__WEBPACK_IMPORTED_MODULE_1__);
        var lodash_uniqBy__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__2(8496);
        var lodash_uniqBy__WEBPACK_IMPORTED_MODULE_2___default = /* @__PURE__ */ __webpack_require__2.n(lodash_uniqBy__WEBPACK_IMPORTED_MODULE_2__);
        var transducers_js__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__2(6046);
        var transducers_js__WEBPACK_IMPORTED_MODULE_3___default = /* @__PURE__ */ __webpack_require__2.n(transducers_js__WEBPACK_IMPORTED_MODULE_3__);
        var _common_html_to_text__WEBPACK_IMPORTED_MODULE_4__ = __webpack_require__2(6305);
        var _common_assert__WEBPACK_IMPORTED_MODULE_5__ = __webpack_require__2(1602);
        var _lib_extract_contact_from_email_contact_string__WEBPACK_IMPORTED_MODULE_6__ = __webpack_require__2(3324);
        function extractGmailThreadIdFromMessageIdSearch(responseString) {
          const threadResponseArray = deserialize(responseString).value;
          const threadIdArrayMarker = "cs";
          const threadIdArray = _searchArray(threadResponseArray, threadIdArrayMarker, (markerArray) => markerArray[0] === "cs" && markerArray.length > 20);
          if (!threadIdArray) {
            return null;
          }
          return threadIdArray[1];
        }
        function rewriteSingleQuotes(s) {
          let i = 0;
          const resultParts = [];
          while (true) {
            const nextQuoteIndex = findNextQuote(s, i);
            if (nextQuoteIndex < 0) {
              resultParts.push(s.substr(i));
              break;
            }
            resultParts.push(s.substr(i, nextQuoteIndex - i));
            resultParts.push('"');
            i = nextQuoteIndex + 1;
            if (s[nextQuoteIndex] === '"') {
              const nextDoubleQuoteIndex = findNextUnescapedCharacter(s, i, '"');
              if (nextDoubleQuoteIndex < 0) {
                throw new Error("Unclosed double quote");
              }
              resultParts.push(s.slice(i, nextDoubleQuoteIndex + 1));
              i = nextDoubleQuoteIndex + 1;
            } else {
              const nextSingleQuoteIndex = findNextUnescapedCharacter(s, i, "'");
              if (nextSingleQuoteIndex < 0) {
                throw new Error("Unclosed single quote");
              }
              const part = s.slice(i, nextSingleQuoteIndex).replace(/"/g, "\\\"").replace(/\\'/g, "'");
              resultParts.push(part);
              resultParts.push('"');
              i = nextSingleQuoteIndex + 1;
            }
          }
          return resultParts.join("");
        }
        function findNextQuote(s, start) {
          for (let i = start, len = s.length;i < len; i++) {
            if (s[i] === '"' || s[i] === "'") {
              return i;
            }
          }
          return -1;
        }
        function findNextUnescapedCharacter(s, start, char) {
          for (let i = start, len = s.length;i < len; i++) {
            if (s[i] === "\\") {
              i++;
            } else if (s[i] === char) {
              return i;
            }
          }
          return -1;
        }
        function deserialize(threadResponseString) {
          const options = {
            includeLengths: false,
            suggestionMode: /^5\n/.test(threadResponseString),
            noArrayNewLines: !/^[,\]]/m.test(threadResponseString),
            includeExplicitNulls: true
          };
          const value = [];
          let pos;
          if (options.suggestionMode) {
            pos = threadResponseString.indexOf(`'
`);
            if (pos === -1) {
              throw new Error("Message was missing beginning header");
            }
            pos += 2;
          } else {
            pos = threadResponseString.indexOf(`

`);
            if (pos === -1) {
              throw new Error("Message was missing beginning newlines");
            }
            pos += 2;
          }
          while (pos < threadResponseString.length) {
            let lineEnd = threadResponseString.indexOf(`
`, pos + 1);
            if (lineEnd === -1) {
              lineEnd = threadResponseString.length;
            } else if (threadResponseString[lineEnd - 1] === "\r") {
              lineEnd += 1;
            }
            const line = threadResponseString.slice(pos, lineEnd);
            let dataLine;
            if (/^\d+\s*$/.test(line)) {
              options.includeLengths = true;
              const length = +line;
              dataLine = threadResponseString.slice(lineEnd, lineEnd + length);
              pos = lineEnd + length;
            } else {
              dataLine = threadResponseString.slice(pos);
              pos = threadResponseString.length;
            }
            value.push(deserializeArray(dataLine));
          }
          return {
            value,
            options
          };
        }
        function transformUnquotedSections(str, cb) {
          const parts = [];
          let nextQuote;
          let position = 0;
          let inString = false;
          while ((nextQuote = findNextUnescapedCharacter(str, position, '"')) !== -1) {
            if (inString) {
              parts.push(str.slice(position, nextQuote + 1));
            } else {
              parts.push(cb(str.slice(position, nextQuote + 1)));
            }
            position = nextQuote + 1;
            inString = !inString;
          }
          if (inString) {
            throw new Error("string ended inside quoted section");
          }
          parts.push(cb(str.slice(position)));
          return parts.join("");
        }
        function deserializeArray(value) {
          value = value.replace(/[\r\n\t]/g, "");
          value = rewriteSingleQuotes(value);
          value = transformUnquotedSections(value, (match) => match.replace(/,\s*(?=,|\])/g, ",null").replace(/\[\s*(?=,)/g, "[null"));
          try {
            return JSON.parse(value, (k, v) => v == null ? undefined : v);
          } catch (err) {
            throw new Error("deserialization error");
          }
        }
        function serialize(value, options) {
          if (options.suggestionMode) {
            (0, _common_assert__WEBPACK_IMPORTED_MODULE_5__.v)(options.includeLengths);
            return suggestionSerialize(value, options.includeExplicitNulls);
          }
          return threadListSerialize(value, options);
        }
        function threadListSerialize(threadResponseArray, options) {
          const {
            includeLengths,
            noArrayNewLines,
            includeExplicitNulls
          } = options;
          let response = `)]}'
` + (noArrayNewLines && includeLengths ? "" : `
`);
          for (let ii = 0;ii < threadResponseArray.length; ii++) {
            const arraySection = threadResponseArray[ii];
            const arraySectionString = serializeArray(arraySection, noArrayNewLines, includeExplicitNulls);
            if (!includeLengths) {
              response += arraySectionString;
            } else {
              const length = arraySectionString.length + (noArrayNewLines ? 2 : 1);
              response += (noArrayNewLines ? `
` : "") + length + `
` + arraySectionString;
            }
          }
          if (!includeLengths) {
            if (!noArrayNewLines) {
              const lines = response.split(/\r|\n/);
              const firstLines = lines.slice(0, -3);
              const lastLines = lines.slice(-3);
              response = firstLines.join(`
`);
              response += `
` + lastLines[0] + lastLines[1].replace(/"/g, "'");
            } else {
              response = response.replace(/"([0-9a-f]{8,16})"\]$/, "'$1']");
            }
          }
          return response + (noArrayNewLines && includeLengths ? `
` : "");
        }
        function suggestionSerialize(suggestionsArray, includeExplicitNulls) {
          let response = `5
)]}'
`;
          for (let ii = 0;ii < suggestionsArray.length; ii++) {
            const arraySection = suggestionsArray[ii];
            const arraySectionString = serializeArray(arraySection, false, includeExplicitNulls);
            const length = arraySectionString.length;
            response += length + `\r
` + arraySectionString;
          }
          return response;
        }
        function serializeArray(array, noArrayNewLines, includeExplicitNulls) {
          let response = "[";
          for (let ii = 0;ii < array.length; ii++) {
            const item = array[ii];
            let addition;
            if (Array.isArray(item)) {
              addition = serializeArray(item, noArrayNewLines, includeExplicitNulls);
            } else if (item == null) {
              addition = includeExplicitNulls ? "null" : "";
            } else {
              addition = JSON.stringify(item).replace(/</gim, "\\u003c").replace(/=/gim, "\\u003d").replace(/>/gim, "\\u003e").replace(/&/gim, "\\u0026");
            }
            if (ii > 0) {
              response += ",";
            }
            response += addition;
          }
          response += "]" + (noArrayNewLines ? "" : `
`);
          return response;
        }
        function readDraftId(response, messageID) {
          const decoded = deserialize(response).value;
          const msgA = t.toArray(decoded, t.compose(t.cat, t.filter(Array.isArray), t.cat, t.filter((x) => x[0] === "ms" && x[1] === messageID), t.take(1), t.map((x) => x[60])))[0];
          if (msgA) {
            const match = msgA.match(/^msg-[^:]:(\S+)$/i);
            return match && match[1];
          }
          return null;
        }
        function replaceThreadsInResponse(response, replacementThreads, _ref) {
          let {
            start,
            total
          } = _ref;
          const {
            value,
            options
          } = deserialize(response);
          const actionResponseMode = value.length === 1 && value[0].length === 2 && typeof value[0][1] === "string";
          const threadValue = actionResponseMode ? value[0][0].map((x) => [x]) : value;
          const preTbGroups = [];
          const postTbGroups = [];
          let preTbItems = [];
          let postTbItems = [];
          let hasSeenTb = false;
          threadValue.forEach((group) => {
            let tbSeenInThisGroup = false;
            const preTbGroup = [];
            const postTbGroup = [];
            group.forEach((item) => {
              if (total && item[0] === "ti") {
                if (typeof total === "number") {
                  item[2] = item[10] = total;
                } else if (total === "MANY") {
                  item[2] = item[10] = 100 * 1000;
                  item[3] = 1;
                  const query = item[5];
                  if (item[6]) {
                    item[6][0] = [query, 1];
                  } else {
                    console.error("replaceThreadsInResponse(): Missing item[6]");
                  }
                }
              }
              if (item[0] === "tb") {
                hasSeenTb = tbSeenInThisGroup = true;
                if (preTbGroup.length) {
                  preTbItems = preTbGroup;
                }
                postTbItems = postTbGroup;
              } else if (!hasSeenTb) {
                preTbGroup.push(item);
              } else {
                postTbGroup.push(item);
              }
            });
            if (!tbSeenInThisGroup) {
              if (!hasSeenTb) {
                preTbGroups.push(preTbGroup);
              } else {
                postTbGroups.push(postTbGroup);
              }
            }
          });
          const newTbs = _threadsToTbGroups(replacementThreads, start);
          if (preTbItems.length) {
            newTbs[0] = preTbItems.concat(newTbs[0] || []);
          }
          if (postTbItems.length) {
            if (newTbs.length) {
              newTbs[newTbs.length - 1] = newTbs[newTbs.length - 1].concat(postTbItems);
            } else {
              newTbs.push(postTbItems);
            }
          }
          const parsedNew = flatten([preTbGroups, newTbs, postTbGroups]);
          const allSections = flatten(parsedNew);
          const endSection = last(allSections);
          if (endSection[0] !== "e") {
            throw new Error("Failed to find end section");
          }
          endSection[1] = allSections.length;
          const fullNew = actionResponseMode ? [[flatten(parsedNew), value[0][1]]] : parsedNew;
          return serialize(fullNew, options);
        }
        function extractThreads(response) {
          return extractThreadsFromDeserialized(deserialize(response).value);
        }
        function extractThreadsFromDeserialized(value) {
          if (value.length === 1 && value[0].length === 2 && typeof value[0][1] === "string") {
            value = [value[0][0]];
          }
          return _extractThreadArraysFromResponseArray(value).map((thread) => Object.freeze(Object.defineProperty({
            subject: (0, _common_html_to_text__WEBPACK_IMPORTED_MODULE_4__.A)(thread[9]),
            shortDate: (0, _common_html_to_text__WEBPACK_IMPORTED_MODULE_4__.A)(thread[14]),
            timeString: (0, _common_html_to_text__WEBPACK_IMPORTED_MODULE_4__.A)(thread[15]),
            peopleHtml: cleanupPeopleLine(thread[7]),
            timestamp: thread[16] / 1000,
            isUnread: thread[9].indexOf("<b>") > -1,
            lastEmailAddress: thread[28],
            bodyPreviewHtml: thread[10],
            someGmailMessageIds: [thread[1], thread[2]],
            gmailThreadId: thread[0]
          }, "_originalGmailFormat", {
            value: thread
          })));
        }
        const _extractMessageIdsFromThreadBatchRequestXf = transducers_js__WEBPACK_IMPORTED_MODULE_3___default().compose(transducers_js__WEBPACK_IMPORTED_MODULE_3___default().cat, transducers_js__WEBPACK_IMPORTED_MODULE_3___default().cat, transducers_js__WEBPACK_IMPORTED_MODULE_3___default().filter((item) => item[0] === "cs"), transducers_js__WEBPACK_IMPORTED_MODULE_3___default().map((item) => [item[1], item[2]]));
        function extractMessageIdsFromThreadBatchRequest(response) {
          const {
            value
          } = deserialize(response);
          return t.toObj(value, _extractMessageIdsFromThreadBatchRequestXf);
        }
        function cleanupPeopleLine(peopleHtml) {
          return peopleHtml.replace(/^[^<]*/, "").replace(/(<span[^>]*) class="[^"]*"/g, "$1");
        }
        const _extractThreadArraysFromResponseArrayXf = transducers_js__WEBPACK_IMPORTED_MODULE_3___default().compose(transducers_js__WEBPACK_IMPORTED_MODULE_3___default().cat, transducers_js__WEBPACK_IMPORTED_MODULE_3___default().filter((item) => item[0] === "tb"), transducers_js__WEBPACK_IMPORTED_MODULE_3___default().map((item) => item[2]), transducers_js__WEBPACK_IMPORTED_MODULE_3___default().cat);
        function _extractThreadArraysFromResponseArray(threadResponseArray) {
          return transducers_js__WEBPACK_IMPORTED_MODULE_3___default().toArray(threadResponseArray, _extractThreadArraysFromResponseArrayXf);
        }
        const _extractThreadsFromConversationViewResponseArrayXf = transducers_js__WEBPACK_IMPORTED_MODULE_3___default().compose(transducers_js__WEBPACK_IMPORTED_MODULE_3___default().cat, transducers_js__WEBPACK_IMPORTED_MODULE_3___default().filter((item) => item[0] === "cs"), transducers_js__WEBPACK_IMPORTED_MODULE_3___default().map((item) => ({
          threadID: item[1],
          messageIDs: item[8]
        })));
        const _extractMessagesFromResponseArrayXf = transducers_js__WEBPACK_IMPORTED_MODULE_3___default().compose(transducers_js__WEBPACK_IMPORTED_MODULE_3___default().cat, transducers_js__WEBPACK_IMPORTED_MODULE_3___default().filter((item) => item[0] === "ms"), transducers_js__WEBPACK_IMPORTED_MODULE_3___default().map((item) => {
          const m = {
            messageID: item[1],
            date: item[7],
            recipients: undefined
          };
          if (Array.isArray(item[13])) {
            m.recipients = item[13].slice(1, 4).filter((b) => b != null).flat().map(_lib_extract_contact_from_email_contact_string__WEBPACK_IMPORTED_MODULE_6__.A);
          } else if (Array.isArray(item[37])) {
            m.recipients = lodash_uniqBy__WEBPACK_IMPORTED_MODULE_2___default()(item[37].slice(0, 5).filter((b) => Array.isArray(b)).flat(), (b) => b[1]).map((b) => ({
              emailAddress: b[1],
              name: b[0] ?? undefined
            }));
          }
          return m;
        }));
        function extractMessages(response) {
          let {
            value
          } = deserialize(response);
          if (value.length === 1)
            value = value[0];
          const threads = transducers_js__WEBPACK_IMPORTED_MODULE_3___default().toArray(value, _extractThreadsFromConversationViewResponseArrayXf);
          const messages = transducers_js__WEBPACK_IMPORTED_MODULE_3___default().toArray(value, _extractMessagesFromResponseArrayXf);
          const messageMap = {};
          messages.forEach((message) => {
            if (message.messageID != null) {
              messageMap[message.messageID] = message;
            }
          });
          return threads.map((_ref2) => {
            let {
              threadID,
              messageIDs
            } = _ref2;
            return {
              threadID,
              messages: messageIDs.map((messageID) => messageMap[messageID])
            };
          });
        }
        function _threadsToTbGroups(threads, start) {
          const _threadsToTbGroupsXf = t.compose(t.map((thread) => thread._originalGmailFormat), t.partition(10), mapIndexed((threadsChunk, i) => [["tb", start + i * 10, threadsChunk]]));
          return t.toArray(threads, _threadsToTbGroupsXf);
        }
        function _searchArray(responseArray, marker, markerArrayValidator) {
          const pathArray = _searchObject(responseArray, marker, 100);
          for (let ii = 0;ii < pathArray.length; ii++) {
            const pathToMarkerArray = pathArray[ii].path.slice(0, -1);
            const markerArray = _getArrayValueFromPath(responseArray, pathToMarkerArray);
            if (markerArrayValidator(markerArray)) {
              return markerArray;
            }
          }
        }
        function _searchObject(element, query, maxDepth) {
          const retVal = [];
          const initialNode = {
            el: element,
            path: []
          };
          const nodeList = [initialNode];
          while (nodeList.length > 0) {
            const node = nodeList.pop();
            if (node.path.length <= maxDepth) {
              if (node.el !== null && typeof node.el === "object") {
                const keys = Object.keys(node.el);
                for (let i = 0;i < keys.length; i++) {
                  const key = keys[i];
                  const newNode = {
                    el: node.el[key],
                    path: node.path.concat([key])
                  };
                  nodeList.push(newNode);
                }
              } else {
                if (node.el === query) {
                  retVal.push(node);
                }
              }
            }
          }
          return retVal;
        }
        function _getArrayValueFromPath(responseArray, path) {
          let currentArray = responseArray;
          for (let ii = 0;ii < path.length; ii++) {
            currentArray = currentArray[path[ii]];
          }
          return currentArray;
        }
      },
      8105: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => getAccountUrlPart
        });
        function getAccountUrlPart() {
          const delegatedAccountMatch = document.location.pathname.match(/\/b\/(.+?)\/u\/(\d+)/);
          if (delegatedAccountMatch) {
            const delegatedAccountId = delegatedAccountMatch[1];
            const delegatedAccountNumber = delegatedAccountMatch[2];
            return `/u/${delegatedAccountNumber}/d/${delegatedAccountId}`;
          } else {
            const accountParamMatch = document.location.pathname.match(/(\/u\/\d+)\//i);
            const accountParam = accountParamMatch ? accountParamMatch[1] : "/u/0";
            return accountParam;
          }
        }
      },
      5609: (module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => driver_common_gmailAjax
        });
        var js = __webpack_require__2(7332);
        var kefir_esm = __webpack_require__2(7249);
        function imageRequest(url) {
          return new Promise((resolve, reject) => {
            const img = new Image;
            img.addEventListener("load", () => resolve(img));
            img.addEventListener("error", reject);
            img.src = url;
          });
        }
        function rateLimitQueuer(fn, period, count) {
          let callTimestamps = [];
          const queue = [];
          let runningQueue = false;
          function runJob() {
            const job = queue.shift();
            job();
            if (queue.length) {
              runQueue();
            } else {
              runningQueue = false;
            }
          }
          function runQueue() {
            runningQueue = true;
            const timeToWait = getTimeToUnqueueItem();
            if (timeToWait > 0) {
              setTimeout(runJob, timeToWait);
            } else {
              runJob();
            }
          }
          function getTimeToUnqueueItem() {
            const now = Date.now();
            const periodAgo = now - period;
            callTimestamps = callTimestamps.filter((time) => time > periodAgo);
            if (callTimestamps.length >= count) {
              return callTimestamps[0] - periodAgo;
            }
            return -1;
          }
          return function attempt() {
            for (var _len = arguments.length, args = new Array(_len), _key = 0;_key < _len; _key++) {
              args[_key] = arguments[_key];
            }
            let job;
            const promise = new Promise((resolve, reject) => {
              job = () => {
                callTimestamps.push(Date.now());
                try {
                  resolve(fn.apply(this, args));
                } catch (err) {
                  reject(err);
                }
              };
            });
            if (!job)
              throw new Error("Should not happen");
            queue.push(job);
            if (!runningQueue) {
              runQueue();
            }
            return promise;
          };
        }
        var ajax = __webpack_require__2(8587);
        module = __webpack_require__2.hmd(module);
        const IMAGE_REQUEST_TIMEOUT = 1000 * 60;
        const limitedAjax = rateLimitQueuer(rateLimitQueuer(ajax.A, 1000, 7), 10 * 1000, 50);
        async function gmailAjax(opts) {
          if (!/^https:\/\/mail\.google\.com(?:$|\/)/.test(opts.url)) {
            throw new Error("Should not happen: gmailAjax called with non-gmail url");
          }
          if (document.location.origin === "https://mail.google.com") {
            return await limitedAjax(opts);
          }
          try {
            return await limitedAjax({
              ...opts,
              canRetry: false
            });
          } catch (e) {
            if (e && e.status === 0) {
              try {
                await kefir_esm["default"].fromPromise(imageRequest("https://mail.google.com/mail/u/0/")).merge(kefir_esm["default"].later(IMAGE_REQUEST_TIMEOUT, undefined)).take(1).takeErrors(1).toPromise();
              } catch (e2) {}
              return await limitedAjax(opts);
            } else if (e && typeof e.status === "number" && e.status >= 500) {
              return await limitedAjax(opts);
            } else {
              throw e;
            }
          }
        }
        const driver_common_gmailAjax = (0, js.defn)(module, gmailAjax);
      },
      5355: (module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => __WEBPACK_DEFAULT_EXPORT__
        });
        var ud__WEBPACK_IMPORTED_MODULE_3__ = __webpack_require__2(7332);
        var querystring__WEBPACK_IMPORTED_MODULE_0__ = __webpack_require__2(6448);
        var _gmailAjax__WEBPACK_IMPORTED_MODULE_1__ = __webpack_require__2(5609);
        var _getAccountUrlPart__WEBPACK_IMPORTED_MODULE_2__ = __webpack_require__2(8105);
        module = __webpack_require__2.hmd(module);
        async function requestGmailThread(ikValue, threadId) {
          const queryParameters = {
            ui: 2,
            ik: ikValue,
            view: "cv",
            th: threadId,
            pcd: 1,
            mb: 0,
            rt: "c",
            search: "inbox",
            type: threadId
          };
          const {
            text
          } = await (0, _gmailAjax__WEBPACK_IMPORTED_MODULE_1__.A)({
            method: "POST",
            url: `https://mail.google.com/mail${(0, _getAccountUrlPart__WEBPACK_IMPORTED_MODULE_2__.A)()}?${querystring__WEBPACK_IMPORTED_MODULE_0__.stringify(queryParameters)}`,
            canRetry: true
          });
          return text;
        }
        const __WEBPACK_DEFAULT_EXPORT__ = (0, ud__WEBPACK_IMPORTED_MODULE_3__.defn)(module, requestGmailThread);
      },
      3324: (__unused_webpack_module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          A: () => extractContactFromEmailContactString
        });
        const re = /^([^<>()[\]\\,;:\s"]+|".+")@[^\s<>]+$/;
        function isValidEmail(candidate) {
          if (candidate == null) {
            return false;
          }
          return re.test(candidate);
        }
        function extractContactFromEmailContactString(contactInfoString) {
          let name;
          let emailAddress = null;
          const contactInfoParts = contactInfoString.split("<");
          const firstPartTrimmed = contactInfoParts[0].replace(/\u202c/g, "").trim();
          if (contactInfoParts.length > 1) {
            name = firstPartTrimmed;
            emailAddress = contactInfoParts[1].split(">")[0].replace(/\u202c/g, "").trim();
          } else {
            if (isValidEmail(firstPartTrimmed)) {
              emailAddress = firstPartTrimmed;
            } else {
              throw Object.assign(new Error("Invalid email address"), {
                firstPartTrimmed
              });
            }
          }
          return {
            name,
            emailAddress
          };
        }
      },
      9060: (module) => {
        module.exports = function newArray(start, end) {
          var n0 = typeof start === "number", n1 = typeof end === "number";
          if (n0 && !n1) {
            end = start;
            start = 0;
          } else if (!n0 && !n1) {
            start = 0;
            end = 0;
          }
          start = start | 0;
          end = end | 0;
          var len = end - start;
          if (len < 0)
            throw new Error("array length must be positive");
          var a = new Array(len);
          for (var i = 0, c = start;i < len; i++, c++)
            a[i] = c;
          return a;
        };
      },
      1812: (module, exports, __webpack_require__2) => {
        Object.defineProperty(exports, "__esModule", {
          value: true
        });
        exports["default"] = autoHtml;
        var _escape = _interopRequireDefault(__webpack_require__2(3131));
        function _interopRequireDefault(obj) {
          return obj && obj.__esModule ? obj : { default: obj };
        }
        function autoHtml(templateParts) {
          var parts = new Array(templateParts.length * 2 - 1);
          parts[0] = templateParts[0];
          for (var i = 0, len = arguments.length <= 1 ? 0 : arguments.length - 1;i < len; i++) {
            var value = i + 1 < 1 || arguments.length <= i + 1 ? undefined : arguments[i + 1];
            parts[2 * i + 1] = value && Object.prototype.hasOwnProperty.call(value, "__html") ? value.__html : (0, _escape.default)(value);
            parts[2 * i + 2] = templateParts[i + 1];
          }
          return parts.join("");
        }
        module.exports = exports.default;
        module.exports["default"] = exports.default;
      },
      2180: function(module, exports, __webpack_require__2) {
        var __WEBPACK_AMD_DEFINE_RESULT__;
        (function(globalObject) {
          var BigNumber, isNumeric = /^-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i, mathceil = Math.ceil, mathfloor = Math.floor, bignumberError = "[BigNumber Error] ", tooManyDigits = bignumberError + "Number primitive has more than 15 significant digits: ", BASE = 100000000000000, LOG_BASE = 14, MAX_SAFE_INTEGER = 9007199254740991, POWS_TEN = [1, 10, 100, 1000, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 10000000000, 100000000000, 1000000000000, 10000000000000], SQRT_BASE = 1e7, MAX = 1e9;
          function clone(configObject) {
            var div, convertBase, parseNumeric, P = BigNumber2.prototype = { constructor: BigNumber2, toString: null, valueOf: null }, ONE = new BigNumber2(1), DECIMAL_PLACES = 20, ROUNDING_MODE = 4, TO_EXP_NEG = -7, TO_EXP_POS = 21, MIN_EXP = -1e7, MAX_EXP = 1e7, CRYPTO = false, MODULO_MODE = 1, POW_PRECISION = 0, FORMAT = {
              prefix: "",
              groupSize: 3,
              secondaryGroupSize: 0,
              groupSeparator: ",",
              decimalSeparator: ".",
              fractionGroupSize: 0,
              fractionGroupSeparator: " ",
              suffix: ""
            }, ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz";
            function BigNumber2(v, b) {
              var alphabet, c, caseChanged, e, i, isNum, len, str, x = this;
              if (!(x instanceof BigNumber2))
                return new BigNumber2(v, b);
              if (b == null) {
                if (v && v._isBigNumber === true) {
                  x.s = v.s;
                  if (!v.c || v.e > MAX_EXP) {
                    x.c = x.e = null;
                  } else if (v.e < MIN_EXP) {
                    x.c = [x.e = 0];
                  } else {
                    x.e = v.e;
                    x.c = v.c.slice();
                  }
                  return;
                }
                if ((isNum = typeof v == "number") && v * 0 == 0) {
                  x.s = 1 / v < 0 ? (v = -v, -1) : 1;
                  if (v === ~~v) {
                    for (e = 0, i = v;i >= 10; i /= 10, e++)
                      ;
                    if (e > MAX_EXP) {
                      x.c = x.e = null;
                    } else {
                      x.e = e;
                      x.c = [v];
                    }
                    return;
                  }
                  str = String(v);
                } else {
                  if (!isNumeric.test(str = String(v)))
                    return parseNumeric(x, str, isNum);
                  x.s = str.charCodeAt(0) == 45 ? (str = str.slice(1), -1) : 1;
                }
                if ((e = str.indexOf(".")) > -1)
                  str = str.replace(".", "");
                if ((i = str.search(/e/i)) > 0) {
                  if (e < 0)
                    e = i;
                  e += +str.slice(i + 1);
                  str = str.substring(0, i);
                } else if (e < 0) {
                  e = str.length;
                }
              } else {
                intCheck(b, 2, ALPHABET.length, "Base");
                if (b == 10) {
                  x = new BigNumber2(v);
                  return round(x, DECIMAL_PLACES + x.e + 1, ROUNDING_MODE);
                }
                str = String(v);
                if (isNum = typeof v == "number") {
                  if (v * 0 != 0)
                    return parseNumeric(x, str, isNum, b);
                  x.s = 1 / v < 0 ? (str = str.slice(1), -1) : 1;
                  if (BigNumber2.DEBUG && str.replace(/^0\.0*|\./, "").length > 15) {
                    throw Error(tooManyDigits + v);
                  }
                } else {
                  x.s = str.charCodeAt(0) === 45 ? (str = str.slice(1), -1) : 1;
                }
                alphabet = ALPHABET.slice(0, b);
                e = i = 0;
                for (len = str.length;i < len; i++) {
                  if (alphabet.indexOf(c = str.charAt(i)) < 0) {
                    if (c == ".") {
                      if (i > e) {
                        e = len;
                        continue;
                      }
                    } else if (!caseChanged) {
                      if (str == str.toUpperCase() && (str = str.toLowerCase()) || str == str.toLowerCase() && (str = str.toUpperCase())) {
                        caseChanged = true;
                        i = -1;
                        e = 0;
                        continue;
                      }
                    }
                    return parseNumeric(x, String(v), isNum, b);
                  }
                }
                isNum = false;
                str = convertBase(str, b, 10, x.s);
                if ((e = str.indexOf(".")) > -1)
                  str = str.replace(".", "");
                else
                  e = str.length;
              }
              for (i = 0;str.charCodeAt(i) === 48; i++)
                ;
              for (len = str.length;str.charCodeAt(--len) === 48; )
                ;
              if (str = str.slice(i, ++len)) {
                len -= i;
                if (isNum && BigNumber2.DEBUG && len > 15 && (v > MAX_SAFE_INTEGER || v !== mathfloor(v))) {
                  throw Error(tooManyDigits + x.s * v);
                }
                if ((e = e - i - 1) > MAX_EXP) {
                  x.c = x.e = null;
                } else if (e < MIN_EXP) {
                  x.c = [x.e = 0];
                } else {
                  x.e = e;
                  x.c = [];
                  i = (e + 1) % LOG_BASE;
                  if (e < 0)
                    i += LOG_BASE;
                  if (i < len) {
                    if (i)
                      x.c.push(+str.slice(0, i));
                    for (len -= LOG_BASE;i < len; ) {
                      x.c.push(+str.slice(i, i += LOG_BASE));
                    }
                    i = LOG_BASE - (str = str.slice(i)).length;
                  } else {
                    i -= len;
                  }
                  for (;i--; str += "0")
                    ;
                  x.c.push(+str);
                }
              } else {
                x.c = [x.e = 0];
              }
            }
            BigNumber2.clone = clone;
            BigNumber2.ROUND_UP = 0;
            BigNumber2.ROUND_DOWN = 1;
            BigNumber2.ROUND_CEIL = 2;
            BigNumber2.ROUND_FLOOR = 3;
            BigNumber2.ROUND_HALF_UP = 4;
            BigNumber2.ROUND_HALF_DOWN = 5;
            BigNumber2.ROUND_HALF_EVEN = 6;
            BigNumber2.ROUND_HALF_CEIL = 7;
            BigNumber2.ROUND_HALF_FLOOR = 8;
            BigNumber2.EUCLID = 9;
            BigNumber2.config = BigNumber2.set = function(obj) {
              var p, v;
              if (obj != null) {
                if (typeof obj == "object") {
                  if (obj.hasOwnProperty(p = "DECIMAL_PLACES")) {
                    v = obj[p];
                    intCheck(v, 0, MAX, p);
                    DECIMAL_PLACES = v;
                  }
                  if (obj.hasOwnProperty(p = "ROUNDING_MODE")) {
                    v = obj[p];
                    intCheck(v, 0, 8, p);
                    ROUNDING_MODE = v;
                  }
                  if (obj.hasOwnProperty(p = "EXPONENTIAL_AT")) {
                    v = obj[p];
                    if (v && v.pop) {
                      intCheck(v[0], -MAX, 0, p);
                      intCheck(v[1], 0, MAX, p);
                      TO_EXP_NEG = v[0];
                      TO_EXP_POS = v[1];
                    } else {
                      intCheck(v, -MAX, MAX, p);
                      TO_EXP_NEG = -(TO_EXP_POS = v < 0 ? -v : v);
                    }
                  }
                  if (obj.hasOwnProperty(p = "RANGE")) {
                    v = obj[p];
                    if (v && v.pop) {
                      intCheck(v[0], -MAX, -1, p);
                      intCheck(v[1], 1, MAX, p);
                      MIN_EXP = v[0];
                      MAX_EXP = v[1];
                    } else {
                      intCheck(v, -MAX, MAX, p);
                      if (v) {
                        MIN_EXP = -(MAX_EXP = v < 0 ? -v : v);
                      } else {
                        throw Error(bignumberError + p + " cannot be zero: " + v);
                      }
                    }
                  }
                  if (obj.hasOwnProperty(p = "CRYPTO")) {
                    v = obj[p];
                    if (v === !!v) {
                      if (v) {
                        if (typeof crypto != "undefined" && crypto && (crypto.getRandomValues || crypto.randomBytes)) {
                          CRYPTO = v;
                        } else {
                          CRYPTO = !v;
                          throw Error(bignumberError + "crypto unavailable");
                        }
                      } else {
                        CRYPTO = v;
                      }
                    } else {
                      throw Error(bignumberError + p + " not true or false: " + v);
                    }
                  }
                  if (obj.hasOwnProperty(p = "MODULO_MODE")) {
                    v = obj[p];
                    intCheck(v, 0, 9, p);
                    MODULO_MODE = v;
                  }
                  if (obj.hasOwnProperty(p = "POW_PRECISION")) {
                    v = obj[p];
                    intCheck(v, 0, MAX, p);
                    POW_PRECISION = v;
                  }
                  if (obj.hasOwnProperty(p = "FORMAT")) {
                    v = obj[p];
                    if (typeof v == "object")
                      FORMAT = v;
                    else
                      throw Error(bignumberError + p + " not an object: " + v);
                  }
                  if (obj.hasOwnProperty(p = "ALPHABET")) {
                    v = obj[p];
                    if (typeof v == "string" && !/^.?$|[+\-.\s]|(.).*\1/.test(v)) {
                      ALPHABET = v;
                    } else {
                      throw Error(bignumberError + p + " invalid: " + v);
                    }
                  }
                } else {
                  throw Error(bignumberError + "Object expected: " + obj);
                }
              }
              return {
                DECIMAL_PLACES,
                ROUNDING_MODE,
                EXPONENTIAL_AT: [TO_EXP_NEG, TO_EXP_POS],
                RANGE: [MIN_EXP, MAX_EXP],
                CRYPTO,
                MODULO_MODE,
                POW_PRECISION,
                FORMAT,
                ALPHABET
              };
            };
            BigNumber2.isBigNumber = function(v) {
              if (!v || v._isBigNumber !== true)
                return false;
              if (!BigNumber2.DEBUG)
                return true;
              var i, n, c = v.c, e = v.e, s = v.s;
              out:
                if ({}.toString.call(c) == "[object Array]") {
                  if ((s === 1 || s === -1) && e >= -MAX && e <= MAX && e === mathfloor(e)) {
                    if (c[0] === 0) {
                      if (e === 0 && c.length === 1)
                        return true;
                      break out;
                    }
                    i = (e + 1) % LOG_BASE;
                    if (i < 1)
                      i += LOG_BASE;
                    if (String(c[0]).length == i) {
                      for (i = 0;i < c.length; i++) {
                        n = c[i];
                        if (n < 0 || n >= BASE || n !== mathfloor(n))
                          break out;
                      }
                      if (n !== 0)
                        return true;
                    }
                  }
                } else if (c === null && e === null && (s === null || s === 1 || s === -1)) {
                  return true;
                }
              throw Error(bignumberError + "Invalid BigNumber: " + v);
            };
            BigNumber2.maximum = BigNumber2.max = function() {
              return maxOrMin(arguments, P.lt);
            };
            BigNumber2.minimum = BigNumber2.min = function() {
              return maxOrMin(arguments, P.gt);
            };
            BigNumber2.random = function() {
              var pow2_53 = 9007199254740992;
              var random53bitInt = Math.random() * pow2_53 & 2097151 ? function() {
                return mathfloor(Math.random() * pow2_53);
              } : function() {
                return (Math.random() * 1073741824 | 0) * 8388608 + (Math.random() * 8388608 | 0);
              };
              return function(dp) {
                var a, b, e, k, v, i = 0, c = [], rand = new BigNumber2(ONE);
                if (dp == null)
                  dp = DECIMAL_PLACES;
                else
                  intCheck(dp, 0, MAX);
                k = mathceil(dp / LOG_BASE);
                if (CRYPTO) {
                  if (crypto.getRandomValues) {
                    a = crypto.getRandomValues(new Uint32Array(k *= 2));
                    for (;i < k; ) {
                      v = a[i] * 131072 + (a[i + 1] >>> 11);
                      if (v >= 9000000000000000) {
                        b = crypto.getRandomValues(new Uint32Array(2));
                        a[i] = b[0];
                        a[i + 1] = b[1];
                      } else {
                        c.push(v % 100000000000000);
                        i += 2;
                      }
                    }
                    i = k / 2;
                  } else if (crypto.randomBytes) {
                    a = crypto.randomBytes(k *= 7);
                    for (;i < k; ) {
                      v = (a[i] & 31) * 281474976710656 + a[i + 1] * 1099511627776 + a[i + 2] * 4294967296 + a[i + 3] * 16777216 + (a[i + 4] << 16) + (a[i + 5] << 8) + a[i + 6];
                      if (v >= 9000000000000000) {
                        crypto.randomBytes(7).copy(a, i);
                      } else {
                        c.push(v % 100000000000000);
                        i += 7;
                      }
                    }
                    i = k / 7;
                  } else {
                    CRYPTO = false;
                    throw Error(bignumberError + "crypto unavailable");
                  }
                }
                if (!CRYPTO) {
                  for (;i < k; ) {
                    v = random53bitInt();
                    if (v < 9000000000000000)
                      c[i++] = v % 100000000000000;
                  }
                }
                k = c[--i];
                dp %= LOG_BASE;
                if (k && dp) {
                  v = POWS_TEN[LOG_BASE - dp];
                  c[i] = mathfloor(k / v) * v;
                }
                for (;c[i] === 0; c.pop(), i--)
                  ;
                if (i < 0) {
                  c = [e = 0];
                } else {
                  for (e = -1;c[0] === 0; c.splice(0, 1), e -= LOG_BASE)
                    ;
                  for (i = 1, v = c[0];v >= 10; v /= 10, i++)
                    ;
                  if (i < LOG_BASE)
                    e -= LOG_BASE - i;
                }
                rand.e = e;
                rand.c = c;
                return rand;
              };
            }();
            BigNumber2.sum = function() {
              var i = 1, args = arguments, sum = new BigNumber2(args[0]);
              for (;i < args.length; )
                sum = sum.plus(args[i++]);
              return sum;
            };
            convertBase = function() {
              var decimal = "0123456789";
              function toBaseOut(str, baseIn, baseOut, alphabet) {
                var j, arr = [0], arrL, i = 0, len = str.length;
                for (;i < len; ) {
                  for (arrL = arr.length;arrL--; arr[arrL] *= baseIn)
                    ;
                  arr[0] += alphabet.indexOf(str.charAt(i++));
                  for (j = 0;j < arr.length; j++) {
                    if (arr[j] > baseOut - 1) {
                      if (arr[j + 1] == null)
                        arr[j + 1] = 0;
                      arr[j + 1] += arr[j] / baseOut | 0;
                      arr[j] %= baseOut;
                    }
                  }
                }
                return arr.reverse();
              }
              return function(str, baseIn, baseOut, sign, callerIsToString) {
                var alphabet, d, e, k, r, x, xc, y, i = str.indexOf("."), dp = DECIMAL_PLACES, rm = ROUNDING_MODE;
                if (i >= 0) {
                  k = POW_PRECISION;
                  POW_PRECISION = 0;
                  str = str.replace(".", "");
                  y = new BigNumber2(baseIn);
                  x = y.pow(str.length - i);
                  POW_PRECISION = k;
                  y.c = toBaseOut(toFixedPoint(coeffToString(x.c), x.e, "0"), 10, baseOut, decimal);
                  y.e = y.c.length;
                }
                xc = toBaseOut(str, baseIn, baseOut, callerIsToString ? (alphabet = ALPHABET, decimal) : (alphabet = decimal, ALPHABET));
                e = k = xc.length;
                for (;xc[--k] == 0; xc.pop())
                  ;
                if (!xc[0])
                  return alphabet.charAt(0);
                if (i < 0) {
                  --e;
                } else {
                  x.c = xc;
                  x.e = e;
                  x.s = sign;
                  x = div(x, y, dp, rm, baseOut);
                  xc = x.c;
                  r = x.r;
                  e = x.e;
                }
                d = e + dp + 1;
                i = xc[d];
                k = baseOut / 2;
                r = r || d < 0 || xc[d + 1] != null;
                r = rm < 4 ? (i != null || r) && (rm == 0 || rm == (x.s < 0 ? 3 : 2)) : i > k || i == k && (rm == 4 || r || rm == 6 && xc[d - 1] & 1 || rm == (x.s < 0 ? 8 : 7));
                if (d < 1 || !xc[0]) {
                  str = r ? toFixedPoint(alphabet.charAt(1), -dp, alphabet.charAt(0)) : alphabet.charAt(0);
                } else {
                  xc.length = d;
                  if (r) {
                    for (--baseOut;++xc[--d] > baseOut; ) {
                      xc[d] = 0;
                      if (!d) {
                        ++e;
                        xc = [1].concat(xc);
                      }
                    }
                  }
                  for (k = xc.length;!xc[--k]; )
                    ;
                  for (i = 0, str = "";i <= k; str += alphabet.charAt(xc[i++]))
                    ;
                  str = toFixedPoint(str, e, alphabet.charAt(0));
                }
                return str;
              };
            }();
            div = function() {
              function multiply(x, k, base) {
                var m, temp, xlo, xhi, carry = 0, i = x.length, klo = k % SQRT_BASE, khi = k / SQRT_BASE | 0;
                for (x = x.slice();i--; ) {
                  xlo = x[i] % SQRT_BASE;
                  xhi = x[i] / SQRT_BASE | 0;
                  m = khi * xlo + xhi * klo;
                  temp = klo * xlo + m % SQRT_BASE * SQRT_BASE + carry;
                  carry = (temp / base | 0) + (m / SQRT_BASE | 0) + khi * xhi;
                  x[i] = temp % base;
                }
                if (carry)
                  x = [carry].concat(x);
                return x;
              }
              function compare2(a, b, aL, bL) {
                var i, cmp;
                if (aL != bL) {
                  cmp = aL > bL ? 1 : -1;
                } else {
                  for (i = cmp = 0;i < aL; i++) {
                    if (a[i] != b[i]) {
                      cmp = a[i] > b[i] ? 1 : -1;
                      break;
                    }
                  }
                }
                return cmp;
              }
              function subtract(a, b, aL, base) {
                var i = 0;
                for (;aL--; ) {
                  a[aL] -= i;
                  i = a[aL] < b[aL] ? 1 : 0;
                  a[aL] = i * base + a[aL] - b[aL];
                }
                for (;!a[0] && a.length > 1; a.splice(0, 1))
                  ;
              }
              return function(x, y, dp, rm, base) {
                var cmp, e, i, more, n, prod, prodL, q, qc, rem, remL, rem0, xi, xL, yc0, yL, yz, s = x.s == y.s ? 1 : -1, xc = x.c, yc = y.c;
                if (!xc || !xc[0] || !yc || !yc[0]) {
                  return new BigNumber2(!x.s || !y.s || (xc ? yc && xc[0] == yc[0] : !yc) ? NaN : xc && xc[0] == 0 || !yc ? s * 0 : s / 0);
                }
                q = new BigNumber2(s);
                qc = q.c = [];
                e = x.e - y.e;
                s = dp + e + 1;
                if (!base) {
                  base = BASE;
                  e = bitFloor(x.e / LOG_BASE) - bitFloor(y.e / LOG_BASE);
                  s = s / LOG_BASE | 0;
                }
                for (i = 0;yc[i] == (xc[i] || 0); i++)
                  ;
                if (yc[i] > (xc[i] || 0))
                  e--;
                if (s < 0) {
                  qc.push(1);
                  more = true;
                } else {
                  xL = xc.length;
                  yL = yc.length;
                  i = 0;
                  s += 2;
                  n = mathfloor(base / (yc[0] + 1));
                  if (n > 1) {
                    yc = multiply(yc, n, base);
                    xc = multiply(xc, n, base);
                    yL = yc.length;
                    xL = xc.length;
                  }
                  xi = yL;
                  rem = xc.slice(0, yL);
                  remL = rem.length;
                  for (;remL < yL; rem[remL++] = 0)
                    ;
                  yz = yc.slice();
                  yz = [0].concat(yz);
                  yc0 = yc[0];
                  if (yc[1] >= base / 2)
                    yc0++;
                  do {
                    n = 0;
                    cmp = compare2(yc, rem, yL, remL);
                    if (cmp < 0) {
                      rem0 = rem[0];
                      if (yL != remL)
                        rem0 = rem0 * base + (rem[1] || 0);
                      n = mathfloor(rem0 / yc0);
                      if (n > 1) {
                        if (n >= base)
                          n = base - 1;
                        prod = multiply(yc, n, base);
                        prodL = prod.length;
                        remL = rem.length;
                        while (compare2(prod, rem, prodL, remL) == 1) {
                          n--;
                          subtract(prod, yL < prodL ? yz : yc, prodL, base);
                          prodL = prod.length;
                          cmp = 1;
                        }
                      } else {
                        if (n == 0) {
                          cmp = n = 1;
                        }
                        prod = yc.slice();
                        prodL = prod.length;
                      }
                      if (prodL < remL)
                        prod = [0].concat(prod);
                      subtract(rem, prod, remL, base);
                      remL = rem.length;
                      if (cmp == -1) {
                        while (compare2(yc, rem, yL, remL) < 1) {
                          n++;
                          subtract(rem, yL < remL ? yz : yc, remL, base);
                          remL = rem.length;
                        }
                      }
                    } else if (cmp === 0) {
                      n++;
                      rem = [0];
                    }
                    qc[i++] = n;
                    if (rem[0]) {
                      rem[remL++] = xc[xi] || 0;
                    } else {
                      rem = [xc[xi]];
                      remL = 1;
                    }
                  } while ((xi++ < xL || rem[0] != null) && s--);
                  more = rem[0] != null;
                  if (!qc[0])
                    qc.splice(0, 1);
                }
                if (base == BASE) {
                  for (i = 1, s = qc[0];s >= 10; s /= 10, i++)
                    ;
                  round(q, dp + (q.e = i + e * LOG_BASE - 1) + 1, rm, more);
                } else {
                  q.e = e;
                  q.r = +more;
                }
                return q;
              };
            }();
            function format(n, i, rm, id) {
              var c0, e, ne, len, str;
              if (rm == null)
                rm = ROUNDING_MODE;
              else
                intCheck(rm, 0, 8);
              if (!n.c)
                return n.toString();
              c0 = n.c[0];
              ne = n.e;
              if (i == null) {
                str = coeffToString(n.c);
                str = id == 1 || id == 2 && (ne <= TO_EXP_NEG || ne >= TO_EXP_POS) ? toExponential(str, ne) : toFixedPoint(str, ne, "0");
              } else {
                n = round(new BigNumber2(n), i, rm);
                e = n.e;
                str = coeffToString(n.c);
                len = str.length;
                if (id == 1 || id == 2 && (i <= e || e <= TO_EXP_NEG)) {
                  for (;len < i; str += "0", len++)
                    ;
                  str = toExponential(str, e);
                } else {
                  i -= ne;
                  str = toFixedPoint(str, e, "0");
                  if (e + 1 > len) {
                    if (--i > 0)
                      for (str += ".";i--; str += "0")
                        ;
                  } else {
                    i += e - len;
                    if (i > 0) {
                      if (e + 1 == len)
                        str += ".";
                      for (;i--; str += "0")
                        ;
                    }
                  }
                }
              }
              return n.s < 0 && c0 ? "-" + str : str;
            }
            function maxOrMin(args, method) {
              var n, i = 1, m = new BigNumber2(args[0]);
              for (;i < args.length; i++) {
                n = new BigNumber2(args[i]);
                if (!n.s) {
                  m = n;
                  break;
                } else if (method.call(m, n)) {
                  m = n;
                }
              }
              return m;
            }
            function normalise(n, c, e) {
              var i = 1, j = c.length;
              for (;!c[--j]; c.pop())
                ;
              for (j = c[0];j >= 10; j /= 10, i++)
                ;
              if ((e = i + e * LOG_BASE - 1) > MAX_EXP) {
                n.c = n.e = null;
              } else if (e < MIN_EXP) {
                n.c = [n.e = 0];
              } else {
                n.e = e;
                n.c = c;
              }
              return n;
            }
            parseNumeric = function() {
              var basePrefix = /^(-?)0([xbo])(?=\w[\w.]*$)/i, dotAfter = /^([^.]+)\.$/, dotBefore = /^\.([^.]+)$/, isInfinityOrNaN = /^-?(Infinity|NaN)$/, whitespaceOrPlus = /^\s*\+(?=[\w.])|^\s+|\s+$/g;
              return function(x, str, isNum, b) {
                var base, s = isNum ? str : str.replace(whitespaceOrPlus, "");
                if (isInfinityOrNaN.test(s)) {
                  x.s = isNaN(s) ? null : s < 0 ? -1 : 1;
                } else {
                  if (!isNum) {
                    s = s.replace(basePrefix, function(m, p1, p2) {
                      base = (p2 = p2.toLowerCase()) == "x" ? 16 : p2 == "b" ? 2 : 8;
                      return !b || b == base ? p1 : m;
                    });
                    if (b) {
                      base = b;
                      s = s.replace(dotAfter, "$1").replace(dotBefore, "0.$1");
                    }
                    if (str != s)
                      return new BigNumber2(s, base);
                  }
                  if (BigNumber2.DEBUG) {
                    throw Error(bignumberError + "Not a" + (b ? " base " + b : "") + " number: " + str);
                  }
                  x.s = null;
                }
                x.c = x.e = null;
              };
            }();
            function round(x, sd, rm, r) {
              var d, i, j, k, n, ni, rd, xc = x.c, pows10 = POWS_TEN;
              if (xc) {
                out: {
                  for (d = 1, k = xc[0];k >= 10; k /= 10, d++)
                    ;
                  i = sd - d;
                  if (i < 0) {
                    i += LOG_BASE;
                    j = sd;
                    n = xc[ni = 0];
                    rd = n / pows10[d - j - 1] % 10 | 0;
                  } else {
                    ni = mathceil((i + 1) / LOG_BASE);
                    if (ni >= xc.length) {
                      if (r) {
                        for (;xc.length <= ni; xc.push(0))
                          ;
                        n = rd = 0;
                        d = 1;
                        i %= LOG_BASE;
                        j = i - LOG_BASE + 1;
                      } else {
                        break out;
                      }
                    } else {
                      n = k = xc[ni];
                      for (d = 1;k >= 10; k /= 10, d++)
                        ;
                      i %= LOG_BASE;
                      j = i - LOG_BASE + d;
                      rd = j < 0 ? 0 : n / pows10[d - j - 1] % 10 | 0;
                    }
                  }
                  r = r || sd < 0 || xc[ni + 1] != null || (j < 0 ? n : n % pows10[d - j - 1]);
                  r = rm < 4 ? (rd || r) && (rm == 0 || rm == (x.s < 0 ? 3 : 2)) : rd > 5 || rd == 5 && (rm == 4 || r || rm == 6 && (i > 0 ? j > 0 ? n / pows10[d - j] : 0 : xc[ni - 1]) % 10 & 1 || rm == (x.s < 0 ? 8 : 7));
                  if (sd < 1 || !xc[0]) {
                    xc.length = 0;
                    if (r) {
                      sd -= x.e + 1;
                      xc[0] = pows10[(LOG_BASE - sd % LOG_BASE) % LOG_BASE];
                      x.e = -sd || 0;
                    } else {
                      xc[0] = x.e = 0;
                    }
                    return x;
                  }
                  if (i == 0) {
                    xc.length = ni;
                    k = 1;
                    ni--;
                  } else {
                    xc.length = ni + 1;
                    k = pows10[LOG_BASE - i];
                    xc[ni] = j > 0 ? mathfloor(n / pows10[d - j] % pows10[j]) * k : 0;
                  }
                  if (r) {
                    for (;; ) {
                      if (ni == 0) {
                        for (i = 1, j = xc[0];j >= 10; j /= 10, i++)
                          ;
                        j = xc[0] += k;
                        for (k = 1;j >= 10; j /= 10, k++)
                          ;
                        if (i != k) {
                          x.e++;
                          if (xc[0] == BASE)
                            xc[0] = 1;
                        }
                        break;
                      } else {
                        xc[ni] += k;
                        if (xc[ni] != BASE)
                          break;
                        xc[ni--] = 0;
                        k = 1;
                      }
                    }
                  }
                  for (i = xc.length;xc[--i] === 0; xc.pop())
                    ;
                }
                if (x.e > MAX_EXP) {
                  x.c = x.e = null;
                } else if (x.e < MIN_EXP) {
                  x.c = [x.e = 0];
                }
              }
              return x;
            }
            function valueOf(n) {
              var str, e = n.e;
              if (e === null)
                return n.toString();
              str = coeffToString(n.c);
              str = e <= TO_EXP_NEG || e >= TO_EXP_POS ? toExponential(str, e) : toFixedPoint(str, e, "0");
              return n.s < 0 ? "-" + str : str;
            }
            P.absoluteValue = P.abs = function() {
              var x = new BigNumber2(this);
              if (x.s < 0)
                x.s = 1;
              return x;
            };
            P.comparedTo = function(y, b) {
              return compare(this, new BigNumber2(y, b));
            };
            P.decimalPlaces = P.dp = function(dp, rm) {
              var c, n, v, x = this;
              if (dp != null) {
                intCheck(dp, 0, MAX);
                if (rm == null)
                  rm = ROUNDING_MODE;
                else
                  intCheck(rm, 0, 8);
                return round(new BigNumber2(x), dp + x.e + 1, rm);
              }
              if (!(c = x.c))
                return null;
              n = ((v = c.length - 1) - bitFloor(this.e / LOG_BASE)) * LOG_BASE;
              if (v = c[v])
                for (;v % 10 == 0; v /= 10, n--)
                  ;
              if (n < 0)
                n = 0;
              return n;
            };
            P.dividedBy = P.div = function(y, b) {
              return div(this, new BigNumber2(y, b), DECIMAL_PLACES, ROUNDING_MODE);
            };
            P.dividedToIntegerBy = P.idiv = function(y, b) {
              return div(this, new BigNumber2(y, b), 0, 1);
            };
            P.exponentiatedBy = P.pow = function(n, m) {
              var half, isModExp, i, k, more, nIsBig, nIsNeg, nIsOdd, y, x = this;
              n = new BigNumber2(n);
              if (n.c && !n.isInteger()) {
                throw Error(bignumberError + "Exponent not an integer: " + valueOf(n));
              }
              if (m != null)
                m = new BigNumber2(m);
              nIsBig = n.e > 14;
              if (!x.c || !x.c[0] || x.c[0] == 1 && !x.e && x.c.length == 1 || !n.c || !n.c[0]) {
                y = new BigNumber2(Math.pow(+valueOf(x), nIsBig ? 2 - isOdd(n) : +valueOf(n)));
                return m ? y.mod(m) : y;
              }
              nIsNeg = n.s < 0;
              if (m) {
                if (m.c ? !m.c[0] : !m.s)
                  return new BigNumber2(NaN);
                isModExp = !nIsNeg && x.isInteger() && m.isInteger();
                if (isModExp)
                  x = x.mod(m);
              } else if (n.e > 9 && (x.e > 0 || x.e < -1 || (x.e == 0 ? x.c[0] > 1 || nIsBig && x.c[1] >= 240000000 : x.c[0] < 80000000000000 || nIsBig && x.c[0] <= 99999750000000))) {
                k = x.s < 0 && isOdd(n) ? -0 : 0;
                if (x.e > -1)
                  k = 1 / k;
                return new BigNumber2(nIsNeg ? 1 / k : k);
              } else if (POW_PRECISION) {
                k = mathceil(POW_PRECISION / LOG_BASE + 2);
              }
              if (nIsBig) {
                half = new BigNumber2(0.5);
                if (nIsNeg)
                  n.s = 1;
                nIsOdd = isOdd(n);
              } else {
                i = Math.abs(+valueOf(n));
                nIsOdd = i % 2;
              }
              y = new BigNumber2(ONE);
              for (;; ) {
                if (nIsOdd) {
                  y = y.times(x);
                  if (!y.c)
                    break;
                  if (k) {
                    if (y.c.length > k)
                      y.c.length = k;
                  } else if (isModExp) {
                    y = y.mod(m);
                  }
                }
                if (i) {
                  i = mathfloor(i / 2);
                  if (i === 0)
                    break;
                  nIsOdd = i % 2;
                } else {
                  n = n.times(half);
                  round(n, n.e + 1, 1);
                  if (n.e > 14) {
                    nIsOdd = isOdd(n);
                  } else {
                    i = +valueOf(n);
                    if (i === 0)
                      break;
                    nIsOdd = i % 2;
                  }
                }
                x = x.times(x);
                if (k) {
                  if (x.c && x.c.length > k)
                    x.c.length = k;
                } else if (isModExp) {
                  x = x.mod(m);
                }
              }
              if (isModExp)
                return y;
              if (nIsNeg)
                y = ONE.div(y);
              return m ? y.mod(m) : k ? round(y, POW_PRECISION, ROUNDING_MODE, more) : y;
            };
            P.integerValue = function(rm) {
              var n = new BigNumber2(this);
              if (rm == null)
                rm = ROUNDING_MODE;
              else
                intCheck(rm, 0, 8);
              return round(n, n.e + 1, rm);
            };
            P.isEqualTo = P.eq = function(y, b) {
              return compare(this, new BigNumber2(y, b)) === 0;
            };
            P.isFinite = function() {
              return !!this.c;
            };
            P.isGreaterThan = P.gt = function(y, b) {
              return compare(this, new BigNumber2(y, b)) > 0;
            };
            P.isGreaterThanOrEqualTo = P.gte = function(y, b) {
              return (b = compare(this, new BigNumber2(y, b))) === 1 || b === 0;
            };
            P.isInteger = function() {
              return !!this.c && bitFloor(this.e / LOG_BASE) > this.c.length - 2;
            };
            P.isLessThan = P.lt = function(y, b) {
              return compare(this, new BigNumber2(y, b)) < 0;
            };
            P.isLessThanOrEqualTo = P.lte = function(y, b) {
              return (b = compare(this, new BigNumber2(y, b))) === -1 || b === 0;
            };
            P.isNaN = function() {
              return !this.s;
            };
            P.isNegative = function() {
              return this.s < 0;
            };
            P.isPositive = function() {
              return this.s > 0;
            };
            P.isZero = function() {
              return !!this.c && this.c[0] == 0;
            };
            P.minus = function(y, b) {
              var i, j, t2, xLTy, x = this, a = x.s;
              y = new BigNumber2(y, b);
              b = y.s;
              if (!a || !b)
                return new BigNumber2(NaN);
              if (a != b) {
                y.s = -b;
                return x.plus(y);
              }
              var xe = x.e / LOG_BASE, ye = y.e / LOG_BASE, xc = x.c, yc = y.c;
              if (!xe || !ye) {
                if (!xc || !yc)
                  return xc ? (y.s = -b, y) : new BigNumber2(yc ? x : NaN);
                if (!xc[0] || !yc[0]) {
                  return yc[0] ? (y.s = -b, y) : new BigNumber2(xc[0] ? x : ROUNDING_MODE == 3 ? -0 : 0);
                }
              }
              xe = bitFloor(xe);
              ye = bitFloor(ye);
              xc = xc.slice();
              if (a = xe - ye) {
                if (xLTy = a < 0) {
                  a = -a;
                  t2 = xc;
                } else {
                  ye = xe;
                  t2 = yc;
                }
                t2.reverse();
                for (b = a;b--; t2.push(0))
                  ;
                t2.reverse();
              } else {
                j = (xLTy = (a = xc.length) < (b = yc.length)) ? a : b;
                for (a = b = 0;b < j; b++) {
                  if (xc[b] != yc[b]) {
                    xLTy = xc[b] < yc[b];
                    break;
                  }
                }
              }
              if (xLTy)
                t2 = xc, xc = yc, yc = t2, y.s = -y.s;
              b = (j = yc.length) - (i = xc.length);
              if (b > 0)
                for (;b--; xc[i++] = 0)
                  ;
              b = BASE - 1;
              for (;j > a; ) {
                if (xc[--j] < yc[j]) {
                  for (i = j;i && !xc[--i]; xc[i] = b)
                    ;
                  --xc[i];
                  xc[j] += BASE;
                }
                xc[j] -= yc[j];
              }
              for (;xc[0] == 0; xc.splice(0, 1), --ye)
                ;
              if (!xc[0]) {
                y.s = ROUNDING_MODE == 3 ? -1 : 1;
                y.c = [y.e = 0];
                return y;
              }
              return normalise(y, xc, ye);
            };
            P.modulo = P.mod = function(y, b) {
              var q, s, x = this;
              y = new BigNumber2(y, b);
              if (!x.c || !y.s || y.c && !y.c[0]) {
                return new BigNumber2(NaN);
              } else if (!y.c || x.c && !x.c[0]) {
                return new BigNumber2(x);
              }
              if (MODULO_MODE == 9) {
                s = y.s;
                y.s = 1;
                q = div(x, y, 0, 3);
                y.s = s;
                q.s *= s;
              } else {
                q = div(x, y, 0, MODULO_MODE);
              }
              y = x.minus(q.times(y));
              if (!y.c[0] && MODULO_MODE == 1)
                y.s = x.s;
              return y;
            };
            P.multipliedBy = P.times = function(y, b) {
              var c, e, i, j, k, m, xcL, xlo, xhi, ycL, ylo, yhi, zc, base, sqrtBase, x = this, xc = x.c, yc = (y = new BigNumber2(y, b)).c;
              if (!xc || !yc || !xc[0] || !yc[0]) {
                if (!x.s || !y.s || xc && !xc[0] && !yc || yc && !yc[0] && !xc) {
                  y.c = y.e = y.s = null;
                } else {
                  y.s *= x.s;
                  if (!xc || !yc) {
                    y.c = y.e = null;
                  } else {
                    y.c = [0];
                    y.e = 0;
                  }
                }
                return y;
              }
              e = bitFloor(x.e / LOG_BASE) + bitFloor(y.e / LOG_BASE);
              y.s *= x.s;
              xcL = xc.length;
              ycL = yc.length;
              if (xcL < ycL)
                zc = xc, xc = yc, yc = zc, i = xcL, xcL = ycL, ycL = i;
              for (i = xcL + ycL, zc = [];i--; zc.push(0))
                ;
              base = BASE;
              sqrtBase = SQRT_BASE;
              for (i = ycL;--i >= 0; ) {
                c = 0;
                ylo = yc[i] % sqrtBase;
                yhi = yc[i] / sqrtBase | 0;
                for (k = xcL, j = i + k;j > i; ) {
                  xlo = xc[--k] % sqrtBase;
                  xhi = xc[k] / sqrtBase | 0;
                  m = yhi * xlo + xhi * ylo;
                  xlo = ylo * xlo + m % sqrtBase * sqrtBase + zc[j] + c;
                  c = (xlo / base | 0) + (m / sqrtBase | 0) + yhi * xhi;
                  zc[j--] = xlo % base;
                }
                zc[j] = c;
              }
              if (c) {
                ++e;
              } else {
                zc.splice(0, 1);
              }
              return normalise(y, zc, e);
            };
            P.negated = function() {
              var x = new BigNumber2(this);
              x.s = -x.s || null;
              return x;
            };
            P.plus = function(y, b) {
              var t2, x = this, a = x.s;
              y = new BigNumber2(y, b);
              b = y.s;
              if (!a || !b)
                return new BigNumber2(NaN);
              if (a != b) {
                y.s = -b;
                return x.minus(y);
              }
              var xe = x.e / LOG_BASE, ye = y.e / LOG_BASE, xc = x.c, yc = y.c;
              if (!xe || !ye) {
                if (!xc || !yc)
                  return new BigNumber2(a / 0);
                if (!xc[0] || !yc[0])
                  return yc[0] ? y : new BigNumber2(xc[0] ? x : a * 0);
              }
              xe = bitFloor(xe);
              ye = bitFloor(ye);
              xc = xc.slice();
              if (a = xe - ye) {
                if (a > 0) {
                  ye = xe;
                  t2 = yc;
                } else {
                  a = -a;
                  t2 = xc;
                }
                t2.reverse();
                for (;a--; t2.push(0))
                  ;
                t2.reverse();
              }
              a = xc.length;
              b = yc.length;
              if (a - b < 0)
                t2 = yc, yc = xc, xc = t2, b = a;
              for (a = 0;b; ) {
                a = (xc[--b] = xc[b] + yc[b] + a) / BASE | 0;
                xc[b] = BASE === xc[b] ? 0 : xc[b] % BASE;
              }
              if (a) {
                xc = [a].concat(xc);
                ++ye;
              }
              return normalise(y, xc, ye);
            };
            P.precision = P.sd = function(sd, rm) {
              var c, n, v, x = this;
              if (sd != null && sd !== !!sd) {
                intCheck(sd, 1, MAX);
                if (rm == null)
                  rm = ROUNDING_MODE;
                else
                  intCheck(rm, 0, 8);
                return round(new BigNumber2(x), sd, rm);
              }
              if (!(c = x.c))
                return null;
              v = c.length - 1;
              n = v * LOG_BASE + 1;
              if (v = c[v]) {
                for (;v % 10 == 0; v /= 10, n--)
                  ;
                for (v = c[0];v >= 10; v /= 10, n++)
                  ;
              }
              if (sd && x.e + 1 > n)
                n = x.e + 1;
              return n;
            };
            P.shiftedBy = function(k) {
              intCheck(k, -MAX_SAFE_INTEGER, MAX_SAFE_INTEGER);
              return this.times("1e" + k);
            };
            P.squareRoot = P.sqrt = function() {
              var m, n, r, rep, t2, x = this, c = x.c, s = x.s, e = x.e, dp = DECIMAL_PLACES + 4, half = new BigNumber2("0.5");
              if (s !== 1 || !c || !c[0]) {
                return new BigNumber2(!s || s < 0 && (!c || c[0]) ? NaN : c ? x : 1 / 0);
              }
              s = Math.sqrt(+valueOf(x));
              if (s == 0 || s == 1 / 0) {
                n = coeffToString(c);
                if ((n.length + e) % 2 == 0)
                  n += "0";
                s = Math.sqrt(+n);
                e = bitFloor((e + 1) / 2) - (e < 0 || e % 2);
                if (s == 1 / 0) {
                  n = "5e" + e;
                } else {
                  n = s.toExponential();
                  n = n.slice(0, n.indexOf("e") + 1) + e;
                }
                r = new BigNumber2(n);
              } else {
                r = new BigNumber2(s + "");
              }
              if (r.c[0]) {
                e = r.e;
                s = e + dp;
                if (s < 3)
                  s = 0;
                for (;; ) {
                  t2 = r;
                  r = half.times(t2.plus(div(x, t2, dp, 1)));
                  if (coeffToString(t2.c).slice(0, s) === (n = coeffToString(r.c)).slice(0, s)) {
                    if (r.e < e)
                      --s;
                    n = n.slice(s - 3, s + 1);
                    if (n == "9999" || !rep && n == "4999") {
                      if (!rep) {
                        round(t2, t2.e + DECIMAL_PLACES + 2, 0);
                        if (t2.times(t2).eq(x)) {
                          r = t2;
                          break;
                        }
                      }
                      dp += 4;
                      s += 4;
                      rep = 1;
                    } else {
                      if (!+n || !+n.slice(1) && n.charAt(0) == "5") {
                        round(r, r.e + DECIMAL_PLACES + 2, 1);
                        m = !r.times(r).eq(x);
                      }
                      break;
                    }
                  }
                }
              }
              return round(r, r.e + DECIMAL_PLACES + 1, ROUNDING_MODE, m);
            };
            P.toExponential = function(dp, rm) {
              if (dp != null) {
                intCheck(dp, 0, MAX);
                dp++;
              }
              return format(this, dp, rm, 1);
            };
            P.toFixed = function(dp, rm) {
              if (dp != null) {
                intCheck(dp, 0, MAX);
                dp = dp + this.e + 1;
              }
              return format(this, dp, rm);
            };
            P.toFormat = function(dp, rm, format2) {
              var str, x = this;
              if (format2 == null) {
                if (dp != null && rm && typeof rm == "object") {
                  format2 = rm;
                  rm = null;
                } else if (dp && typeof dp == "object") {
                  format2 = dp;
                  dp = rm = null;
                } else {
                  format2 = FORMAT;
                }
              } else if (typeof format2 != "object") {
                throw Error(bignumberError + "Argument not an object: " + format2);
              }
              str = x.toFixed(dp, rm);
              if (x.c) {
                var i, arr = str.split("."), g1 = +format2.groupSize, g2 = +format2.secondaryGroupSize, groupSeparator = format2.groupSeparator || "", intPart = arr[0], fractionPart = arr[1], isNeg = x.s < 0, intDigits = isNeg ? intPart.slice(1) : intPart, len = intDigits.length;
                if (g2)
                  i = g1, g1 = g2, g2 = i, len -= i;
                if (g1 > 0 && len > 0) {
                  i = len % g1 || g1;
                  intPart = intDigits.substr(0, i);
                  for (;i < len; i += g1)
                    intPart += groupSeparator + intDigits.substr(i, g1);
                  if (g2 > 0)
                    intPart += groupSeparator + intDigits.slice(i);
                  if (isNeg)
                    intPart = "-" + intPart;
                }
                str = fractionPart ? intPart + (format2.decimalSeparator || "") + ((g2 = +format2.fractionGroupSize) ? fractionPart.replace(new RegExp("\\d{" + g2 + "}\\B", "g"), "$&" + (format2.fractionGroupSeparator || "")) : fractionPart) : intPart;
              }
              return (format2.prefix || "") + str + (format2.suffix || "");
            };
            P.toFraction = function(md) {
              var d, d0, d1, d2, e, exp, n, n0, n1, q, r, s, x = this, xc = x.c;
              if (md != null) {
                n = new BigNumber2(md);
                if (!n.isInteger() && (n.c || n.s !== 1) || n.lt(ONE)) {
                  throw Error(bignumberError + "Argument " + (n.isInteger() ? "out of range: " : "not an integer: ") + valueOf(n));
                }
              }
              if (!xc)
                return new BigNumber2(x);
              d = new BigNumber2(ONE);
              n1 = d0 = new BigNumber2(ONE);
              d1 = n0 = new BigNumber2(ONE);
              s = coeffToString(xc);
              e = d.e = s.length - x.e - 1;
              d.c[0] = POWS_TEN[(exp = e % LOG_BASE) < 0 ? LOG_BASE + exp : exp];
              md = !md || n.comparedTo(d) > 0 ? e > 0 ? d : n1 : n;
              exp = MAX_EXP;
              MAX_EXP = 1 / 0;
              n = new BigNumber2(s);
              n0.c[0] = 0;
              for (;; ) {
                q = div(n, d, 0, 1);
                d2 = d0.plus(q.times(d1));
                if (d2.comparedTo(md) == 1)
                  break;
                d0 = d1;
                d1 = d2;
                n1 = n0.plus(q.times(d2 = n1));
                n0 = d2;
                d = n.minus(q.times(d2 = d));
                n = d2;
              }
              d2 = div(md.minus(d0), d1, 0, 1);
              n0 = n0.plus(d2.times(n1));
              d0 = d0.plus(d2.times(d1));
              n0.s = n1.s = x.s;
              e = e * 2;
              r = div(n1, d1, e, ROUNDING_MODE).minus(x).abs().comparedTo(div(n0, d0, e, ROUNDING_MODE).minus(x).abs()) < 1 ? [n1, d1] : [n0, d0];
              MAX_EXP = exp;
              return r;
            };
            P.toNumber = function() {
              return +valueOf(this);
            };
            P.toPrecision = function(sd, rm) {
              if (sd != null)
                intCheck(sd, 1, MAX);
              return format(this, sd, rm, 2);
            };
            P.toString = function(b) {
              var str, n = this, s = n.s, e = n.e;
              if (e === null) {
                if (s) {
                  str = "Infinity";
                  if (s < 0)
                    str = "-" + str;
                } else {
                  str = "NaN";
                }
              } else {
                if (b == null) {
                  str = e <= TO_EXP_NEG || e >= TO_EXP_POS ? toExponential(coeffToString(n.c), e) : toFixedPoint(coeffToString(n.c), e, "0");
                } else if (b === 10) {
                  n = round(new BigNumber2(n), DECIMAL_PLACES + e + 1, ROUNDING_MODE);
                  str = toFixedPoint(coeffToString(n.c), n.e, "0");
                } else {
                  intCheck(b, 2, ALPHABET.length, "Base");
                  str = convertBase(toFixedPoint(coeffToString(n.c), e, "0"), 10, b, s, true);
                }
                if (s < 0 && n.c[0])
                  str = "-" + str;
              }
              return str;
            };
            P.valueOf = P.toJSON = function() {
              return valueOf(this);
            };
            P._isBigNumber = true;
            if (configObject != null)
              BigNumber2.set(configObject);
            return BigNumber2;
          }
          function bitFloor(n) {
            var i = n | 0;
            return n > 0 || n === i ? i : i - 1;
          }
          function coeffToString(a) {
            var s, z, i = 1, j = a.length, r = a[0] + "";
            for (;i < j; ) {
              s = a[i++] + "";
              z = LOG_BASE - s.length;
              for (;z--; s = "0" + s)
                ;
              r += s;
            }
            for (j = r.length;r.charCodeAt(--j) === 48; )
              ;
            return r.slice(0, j + 1 || 1);
          }
          function compare(x, y) {
            var a, b, xc = x.c, yc = y.c, i = x.s, j = y.s, k = x.e, l = y.e;
            if (!i || !j)
              return null;
            a = xc && !xc[0];
            b = yc && !yc[0];
            if (a || b)
              return a ? b ? 0 : -j : i;
            if (i != j)
              return i;
            a = i < 0;
            b = k == l;
            if (!xc || !yc)
              return b ? 0 : !xc ^ a ? 1 : -1;
            if (!b)
              return k > l ^ a ? 1 : -1;
            j = (k = xc.length) < (l = yc.length) ? k : l;
            for (i = 0;i < j; i++)
              if (xc[i] != yc[i])
                return xc[i] > yc[i] ^ a ? 1 : -1;
            return k == l ? 0 : k > l ^ a ? 1 : -1;
          }
          function intCheck(n, min, max, name) {
            if (n < min || n > max || n !== mathfloor(n)) {
              throw Error(bignumberError + (name || "Argument") + (typeof n == "number" ? n < min || n > max ? " out of range: " : " not an integer: " : " not a primitive number: ") + String(n));
            }
          }
          function isOdd(n) {
            var k = n.c.length - 1;
            return bitFloor(n.e / LOG_BASE) == k && n.c[k] % 2 != 0;
          }
          function toExponential(str, e) {
            return (str.length > 1 ? str.charAt(0) + "." + str.slice(1) : str) + (e < 0 ? "e" : "e+") + e;
          }
          function toFixedPoint(str, e, z) {
            var len, zs;
            if (e < 0) {
              for (zs = z + ".";++e; zs += z)
                ;
              str = zs + str;
            } else {
              len = str.length;
              if (++e > len) {
                for (zs = z, e -= len;--e; zs += z)
                  ;
                str += zs;
              } else if (e < len) {
                str = str.slice(0, e) + "." + str.slice(e);
              }
            }
            return str;
          }
          BigNumber = clone();
          BigNumber["default"] = BigNumber.BigNumber = BigNumber;
          if (true) {
            __WEBPACK_AMD_DEFINE_RESULT__ = function() {
              return BigNumber;
            }.call(exports, __webpack_require__2, exports, module), __WEBPACK_AMD_DEFINE_RESULT__ !== undefined && (module.exports = __WEBPACK_AMD_DEFINE_RESULT__);
          } else {}
        })(this);
      },
      4785: (module) => {
        var R = typeof Reflect === "object" ? Reflect : null;
        var ReflectApply = R && typeof R.apply === "function" ? R.apply : function ReflectApply2(target, receiver, args) {
          return Function.prototype.apply.call(target, receiver, args);
        };
        var ReflectOwnKeys;
        if (R && typeof R.ownKeys === "function") {
          ReflectOwnKeys = R.ownKeys;
        } else if (Object.getOwnPropertySymbols) {
          ReflectOwnKeys = function ReflectOwnKeys2(target) {
            return Object.getOwnPropertyNames(target).concat(Object.getOwnPropertySymbols(target));
          };
        } else {
          ReflectOwnKeys = function ReflectOwnKeys2(target) {
            return Object.getOwnPropertyNames(target);
          };
        }
        function ProcessEmitWarning(warning) {
          if (console && console.warn)
            console.warn(warning);
        }
        var NumberIsNaN = Number.isNaN || function NumberIsNaN2(value) {
          return value !== value;
        };
        function EventEmitter() {
          EventEmitter.init.call(this);
        }
        module.exports = EventEmitter;
        module.exports.once = once;
        EventEmitter.EventEmitter = EventEmitter;
        EventEmitter.prototype._events = undefined;
        EventEmitter.prototype._eventsCount = 0;
        EventEmitter.prototype._maxListeners = undefined;
        var defaultMaxListeners = 10;
        function checkListener(listener) {
          if (typeof listener !== "function") {
            throw new TypeError('The "listener" argument must be of type Function. Received type ' + typeof listener);
          }
        }
        Object.defineProperty(EventEmitter, "defaultMaxListeners", {
          enumerable: true,
          get: function() {
            return defaultMaxListeners;
          },
          set: function(arg) {
            if (typeof arg !== "number" || arg < 0 || NumberIsNaN(arg)) {
              throw new RangeError('The value of "defaultMaxListeners" is out of range. It must be a non-negative number. Received ' + arg + ".");
            }
            defaultMaxListeners = arg;
          }
        });
        EventEmitter.init = function() {
          if (this._events === undefined || this._events === Object.getPrototypeOf(this)._events) {
            this._events = Object.create(null);
            this._eventsCount = 0;
          }
          this._maxListeners = this._maxListeners || undefined;
        };
        EventEmitter.prototype.setMaxListeners = function setMaxListeners(n) {
          if (typeof n !== "number" || n < 0 || NumberIsNaN(n)) {
            throw new RangeError('The value of "n" is out of range. It must be a non-negative number. Received ' + n + ".");
          }
          this._maxListeners = n;
          return this;
        };
        function _getMaxListeners(that) {
          if (that._maxListeners === undefined)
            return EventEmitter.defaultMaxListeners;
          return that._maxListeners;
        }
        EventEmitter.prototype.getMaxListeners = function getMaxListeners() {
          return _getMaxListeners(this);
        };
        EventEmitter.prototype.emit = function emit(type) {
          var args = [];
          for (var i = 1;i < arguments.length; i++)
            args.push(arguments[i]);
          var doError = type === "error";
          var events = this._events;
          if (events !== undefined)
            doError = doError && events.error === undefined;
          else if (!doError)
            return false;
          if (doError) {
            var er;
            if (args.length > 0)
              er = args[0];
            if (er instanceof Error) {
              throw er;
            }
            var err = new Error("Unhandled error." + (er ? " (" + er.message + ")" : ""));
            err.context = er;
            throw err;
          }
          var handler = events[type];
          if (handler === undefined)
            return false;
          if (typeof handler === "function") {
            ReflectApply(handler, this, args);
          } else {
            var len = handler.length;
            var listeners = arrayClone(handler, len);
            for (var i = 0;i < len; ++i)
              ReflectApply(listeners[i], this, args);
          }
          return true;
        };
        function _addListener(target, type, listener, prepend) {
          var m;
          var events;
          var existing;
          checkListener(listener);
          events = target._events;
          if (events === undefined) {
            events = target._events = Object.create(null);
            target._eventsCount = 0;
          } else {
            if (events.newListener !== undefined) {
              target.emit("newListener", type, listener.listener ? listener.listener : listener);
              events = target._events;
            }
            existing = events[type];
          }
          if (existing === undefined) {
            existing = events[type] = listener;
            ++target._eventsCount;
          } else {
            if (typeof existing === "function") {
              existing = events[type] = prepend ? [listener, existing] : [existing, listener];
            } else if (prepend) {
              existing.unshift(listener);
            } else {
              existing.push(listener);
            }
            m = _getMaxListeners(target);
            if (m > 0 && existing.length > m && !existing.warned) {
              existing.warned = true;
              var w = new Error("Possible EventEmitter memory leak detected. " + existing.length + " " + String(type) + " listeners " + "added. Use emitter.setMaxListeners() to " + "increase limit");
              w.name = "MaxListenersExceededWarning";
              w.emitter = target;
              w.type = type;
              w.count = existing.length;
              ProcessEmitWarning(w);
            }
          }
          return target;
        }
        EventEmitter.prototype.addListener = function addListener(type, listener) {
          return _addListener(this, type, listener, false);
        };
        EventEmitter.prototype.on = EventEmitter.prototype.addListener;
        EventEmitter.prototype.prependListener = function prependListener(type, listener) {
          return _addListener(this, type, listener, true);
        };
        function onceWrapper() {
          if (!this.fired) {
            this.target.removeListener(this.type, this.wrapFn);
            this.fired = true;
            if (arguments.length === 0)
              return this.listener.call(this.target);
            return this.listener.apply(this.target, arguments);
          }
        }
        function _onceWrap(target, type, listener) {
          var state = { fired: false, wrapFn: undefined, target, type, listener };
          var wrapped = onceWrapper.bind(state);
          wrapped.listener = listener;
          state.wrapFn = wrapped;
          return wrapped;
        }
        EventEmitter.prototype.once = function once2(type, listener) {
          checkListener(listener);
          this.on(type, _onceWrap(this, type, listener));
          return this;
        };
        EventEmitter.prototype.prependOnceListener = function prependOnceListener(type, listener) {
          checkListener(listener);
          this.prependListener(type, _onceWrap(this, type, listener));
          return this;
        };
        EventEmitter.prototype.removeListener = function removeListener(type, listener) {
          var list, events, position, i, originalListener;
          checkListener(listener);
          events = this._events;
          if (events === undefined)
            return this;
          list = events[type];
          if (list === undefined)
            return this;
          if (list === listener || list.listener === listener) {
            if (--this._eventsCount === 0)
              this._events = Object.create(null);
            else {
              delete events[type];
              if (events.removeListener)
                this.emit("removeListener", type, list.listener || listener);
            }
          } else if (typeof list !== "function") {
            position = -1;
            for (i = list.length - 1;i >= 0; i--) {
              if (list[i] === listener || list[i].listener === listener) {
                originalListener = list[i].listener;
                position = i;
                break;
              }
            }
            if (position < 0)
              return this;
            if (position === 0)
              list.shift();
            else {
              spliceOne(list, position);
            }
            if (list.length === 1)
              events[type] = list[0];
            if (events.removeListener !== undefined)
              this.emit("removeListener", type, originalListener || listener);
          }
          return this;
        };
        EventEmitter.prototype.off = EventEmitter.prototype.removeListener;
        EventEmitter.prototype.removeAllListeners = function removeAllListeners(type) {
          var listeners, events, i;
          events = this._events;
          if (events === undefined)
            return this;
          if (events.removeListener === undefined) {
            if (arguments.length === 0) {
              this._events = Object.create(null);
              this._eventsCount = 0;
            } else if (events[type] !== undefined) {
              if (--this._eventsCount === 0)
                this._events = Object.create(null);
              else
                delete events[type];
            }
            return this;
          }
          if (arguments.length === 0) {
            var keys = Object.keys(events);
            var key;
            for (i = 0;i < keys.length; ++i) {
              key = keys[i];
              if (key === "removeListener")
                continue;
              this.removeAllListeners(key);
            }
            this.removeAllListeners("removeListener");
            this._events = Object.create(null);
            this._eventsCount = 0;
            return this;
          }
          listeners = events[type];
          if (typeof listeners === "function") {
            this.removeListener(type, listeners);
          } else if (listeners !== undefined) {
            for (i = listeners.length - 1;i >= 0; i--) {
              this.removeListener(type, listeners[i]);
            }
          }
          return this;
        };
        function _listeners(target, type, unwrap) {
          var events = target._events;
          if (events === undefined)
            return [];
          var evlistener = events[type];
          if (evlistener === undefined)
            return [];
          if (typeof evlistener === "function")
            return unwrap ? [evlistener.listener || evlistener] : [evlistener];
          return unwrap ? unwrapListeners(evlistener) : arrayClone(evlistener, evlistener.length);
        }
        EventEmitter.prototype.listeners = function listeners(type) {
          return _listeners(this, type, true);
        };
        EventEmitter.prototype.rawListeners = function rawListeners(type) {
          return _listeners(this, type, false);
        };
        EventEmitter.listenerCount = function(emitter, type) {
          if (typeof emitter.listenerCount === "function") {
            return emitter.listenerCount(type);
          } else {
            return listenerCount.call(emitter, type);
          }
        };
        EventEmitter.prototype.listenerCount = listenerCount;
        function listenerCount(type) {
          var events = this._events;
          if (events !== undefined) {
            var evlistener = events[type];
            if (typeof evlistener === "function") {
              return 1;
            } else if (evlistener !== undefined) {
              return evlistener.length;
            }
          }
          return 0;
        }
        EventEmitter.prototype.eventNames = function eventNames() {
          return this._eventsCount > 0 ? ReflectOwnKeys(this._events) : [];
        };
        function arrayClone(arr, n) {
          var copy = new Array(n);
          for (var i = 0;i < n; ++i)
            copy[i] = arr[i];
          return copy;
        }
        function spliceOne(list, index) {
          for (;index + 1 < list.length; index++)
            list[index] = list[index + 1];
          list.pop();
        }
        function unwrapListeners(arr) {
          var ret = new Array(arr.length);
          for (var i = 0;i < ret.length; ++i) {
            ret[i] = arr[i].listener || arr[i];
          }
          return ret;
        }
        function once(emitter, name) {
          return new Promise(function(resolve, reject) {
            function errorListener(err) {
              emitter.removeListener(name, resolver);
              reject(err);
            }
            function resolver() {
              if (typeof emitter.removeListener === "function") {
                emitter.removeListener("error", errorListener);
              }
              resolve([].slice.call(arguments));
            }
            eventTargetAgnosticAddListener(emitter, name, resolver, { once: true });
            if (name !== "error") {
              addErrorHandlerIfEventEmitter(emitter, errorListener, { once: true });
            }
          });
        }
        function addErrorHandlerIfEventEmitter(emitter, handler, flags) {
          if (typeof emitter.on === "function") {
            eventTargetAgnosticAddListener(emitter, "error", handler, flags);
          }
        }
        function eventTargetAgnosticAddListener(emitter, name, listener, flags) {
          if (typeof emitter.on === "function") {
            if (flags.once) {
              emitter.once(name, listener);
            } else {
              emitter.on(name, listener);
            }
          } else if (typeof emitter.addEventListener === "function") {
            emitter.addEventListener(name, function wrapListener(arg) {
              if (flags.once) {
                emitter.removeEventListener(name, wrapListener);
              }
              listener(arg);
            });
          } else {
            throw new TypeError('The "emitter" argument must be of type EventEmitter. Received type ' + typeof emitter);
          }
        }
      },
      917: (__unused_webpack_module, exports) => {
        Object.defineProperty(exports, "__esModule", {
          value: true
        });
        exports.moduleId = undefined;
        var moduleId = "1f24 e53a";
        exports.moduleId = moduleId;
      },
      4835: (__unused_webpack_module, exports, __webpack_require__2) => {
        var _interopRequireDefault = __webpack_require__2(1654);
        Object.defineProperty(exports, "__esModule", {
          value: true
        });
        exports.init = init;
        var _toConsumableArray2 = _interopRequireDefault(__webpack_require__2(1752));
        var _transferrables = _interopRequireDefault(__webpack_require__2(5440));
        var _moduleId = __webpack_require__2(917);
        function init() {
          var XMLHttpRequest = window.XMLHttpRequest;
          function handler(event) {
            if (!event.data || event.data.type !== "ext-corb-workaround_port" || event.data.moduleId !== _moduleId.moduleId || event.__ext_claimed) {
              return;
            }
            event.__ext_claimed = true;
            window.removeEventListener("message", handler);
            var port = event.data.port;
            var instancesById = {};
            port.addEventListener("message", function(event2) {
              var id = event2.data.id;
              switch (event2.data.type) {
                case "NEW_XHR": {
                  var xhr = instancesById[id] = new XMLHttpRequest;
                  xhr.addEventListener("readystatechange", function() {
                    if (xhr.readyState !== 4) {
                      return;
                    }
                    delete instancesById[id];
                    var responseText;
                    try {
                      responseText = xhr.responseText;
                    } catch (err) {}
                    port.postMessage({
                      type: "COMPLETE",
                      id,
                      headers: xhr.getAllResponseHeaders(),
                      readyState: xhr.readyState,
                      status: xhr.status,
                      statusText: xhr.statusText,
                      responseURL: xhr.responseURL,
                      response: xhr.response,
                      responseText
                    }, (0, _transferrables["default"])([xhr.response]));
                  });
                  break;
                }
                case "SET": {
                  var _event$data = event2.data, prop = _event$data.prop, value = _event$data.value;
                  instancesById[id][prop] = value;
                  break;
                }
                case "CALL": {
                  var _ref;
                  var _event$data2 = event2.data, method = _event$data2.method, args = _event$data2.args;
                  if (method === "abort" && !instancesById[id]) {
                    break;
                  }
                  (_ref = instancesById[id])[method].apply(_ref, (0, _toConsumableArray2["default"])(args));
                  break;
                }
                default: {
                  console.error("ext-corb-workaround: Unknown event in page world:", event2);
                }
              }
            });
            port.addEventListener("messageerror", function(event2) {
              console.error("ext-corb-workaround: Unknown error in page world:", event2);
            });
            port.start();
          }
          window.addEventListener("message", handler);
        }
      },
      5440: (__unused_webpack_module, exports, __webpack_require__2) => {
        var _interopRequireDefault = __webpack_require__2(1654);
        Object.defineProperty(exports, "__esModule", {
          value: true
        });
        exports["default"] = transferrables;
        var _typeof2 = _interopRequireDefault(__webpack_require__2(2990));
        function transferrables(list) {
          return list.map(function(value) {
            if (value && (0, _typeof2["default"])(value) === "object" && value.__proto__) {
              if (value.__proto__.constructor.name === "ArrayBuffer") {
                return value;
              }
              if (value.__proto__.__proto__ && value.__proto__.__proto__.constructor.name === "TypedArray") {
                return value.buffer;
              }
            }
          }).filter(Boolean);
        }
      },
      7249: (module, __webpack_exports__2, __webpack_require__2) => {
        __webpack_require__2.d(__webpack_exports__2, {
          default: () => __WEBPACK_DEFAULT_EXPORT__
        });
        module = __webpack_require__2.hmd(module);
        /*! Kefir.js v3.8.8
         *  https://github.com/kefirjs/kefir
         */
        function createObj(proto) {
          var F = function() {};
          F.prototype = proto;
          return new F;
        }
        function extend(target) {
          var length = arguments.length, i = undefined, prop = undefined;
          for (i = 1;i < length; i++) {
            for (prop in arguments[i]) {
              target[prop] = arguments[i][prop];
            }
          }
          return target;
        }
        function inherit(Child, Parent) {
          var length = arguments.length, i = undefined;
          Child.prototype = createObj(Parent.prototype);
          Child.prototype.constructor = Child;
          for (i = 2;i < length; i++) {
            extend(Child.prototype, arguments[i]);
          }
          return Child;
        }
        var NOTHING = ["<nothing>"];
        var END = "end";
        var VALUE = "value";
        var ERROR = "error";
        var ANY = "any";
        function concat(a, b) {
          var result2 = undefined, length = undefined, i = undefined, j = undefined;
          if (a.length === 0) {
            return b;
          }
          if (b.length === 0) {
            return a;
          }
          j = 0;
          result2 = new Array(a.length + b.length);
          length = a.length;
          for (i = 0;i < length; i++, j++) {
            result2[j] = a[i];
          }
          length = b.length;
          for (i = 0;i < length; i++, j++) {
            result2[j] = b[i];
          }
          return result2;
        }
        function find(arr, value) {
          var length = arr.length, i = undefined;
          for (i = 0;i < length; i++) {
            if (arr[i] === value) {
              return i;
            }
          }
          return -1;
        }
        function findByPred(arr, pred) {
          var length = arr.length, i = undefined;
          for (i = 0;i < length; i++) {
            if (pred(arr[i])) {
              return i;
            }
          }
          return -1;
        }
        function cloneArray(input) {
          var length = input.length, result2 = new Array(length), i = undefined;
          for (i = 0;i < length; i++) {
            result2[i] = input[i];
          }
          return result2;
        }
        function remove(input, index) {
          var length = input.length, result2 = undefined, i = undefined, j = undefined;
          if (index >= 0 && index < length) {
            if (length === 1) {
              return [];
            } else {
              result2 = new Array(length - 1);
              for (i = 0, j = 0;i < length; i++) {
                if (i !== index) {
                  result2[j] = input[i];
                  j++;
                }
              }
              return result2;
            }
          } else {
            return input;
          }
        }
        function map(input, fn) {
          var length = input.length, result2 = new Array(length), i = undefined;
          for (i = 0;i < length; i++) {
            result2[i] = fn(input[i]);
          }
          return result2;
        }
        function forEach(arr, fn) {
          var length = arr.length, i = undefined;
          for (i = 0;i < length; i++) {
            fn(arr[i]);
          }
        }
        function fillArray(arr, value) {
          var length = arr.length, i = undefined;
          for (i = 0;i < length; i++) {
            arr[i] = value;
          }
        }
        function contains(arr, value) {
          return find(arr, value) !== -1;
        }
        function slide(cur, next, max) {
          var length = Math.min(max, cur.length + 1), offset = cur.length - length + 1, result2 = new Array(length), i = undefined;
          for (i = offset;i < length; i++) {
            result2[i - offset] = cur[i];
          }
          result2[length - 1] = next;
          return result2;
        }
        function callSubscriber(type, fn, event) {
          if (type === ANY) {
            fn(event);
          } else if (type === event.type) {
            if (type === VALUE || type === ERROR) {
              fn(event.value);
            } else {
              fn();
            }
          }
        }
        function Dispatcher() {
          this._items = [];
          this._spies = [];
          this._inLoop = 0;
          this._removedItems = null;
        }
        extend(Dispatcher.prototype, {
          add: function(type, fn) {
            this._items = concat(this._items, [{ type, fn }]);
            return this._items.length;
          },
          remove: function(type, fn) {
            var index = findByPred(this._items, function(x) {
              return x.type === type && x.fn === fn;
            });
            if (this._inLoop !== 0 && index !== -1) {
              if (this._removedItems === null) {
                this._removedItems = [];
              }
              this._removedItems.push(this._items[index]);
            }
            this._items = remove(this._items, index);
            return this._items.length;
          },
          addSpy: function(fn) {
            this._spies = concat(this._spies, [fn]);
            return this._spies.length;
          },
          removeSpy: function(fn) {
            this._spies = remove(this._spies, this._spies.indexOf(fn));
            return this._spies.length;
          },
          dispatch: function(event) {
            this._inLoop++;
            for (var i = 0, spies = this._spies;this._spies !== null && i < spies.length; i++) {
              spies[i](event);
            }
            for (var _i = 0, items = this._items;_i < items.length; _i++) {
              if (this._items === null) {
                break;
              }
              if (this._removedItems !== null && contains(this._removedItems, items[_i])) {
                continue;
              }
              callSubscriber(items[_i].type, items[_i].fn, event);
            }
            this._inLoop--;
            if (this._inLoop === 0) {
              this._removedItems = null;
            }
          },
          cleanup: function() {
            this._items = null;
            this._spies = null;
          }
        });
        function Observable() {
          this._dispatcher = new Dispatcher;
          this._active = false;
          this._alive = true;
          this._activating = false;
          this._logHandlers = null;
          this._spyHandlers = null;
        }
        extend(Observable.prototype, {
          _name: "observable",
          _onActivation: function() {},
          _onDeactivation: function() {},
          _setActive: function(active) {
            if (this._active !== active) {
              this._active = active;
              if (active) {
                this._activating = true;
                this._onActivation();
                this._activating = false;
              } else {
                this._onDeactivation();
              }
            }
          },
          _clear: function() {
            this._setActive(false);
            this._dispatcher.cleanup();
            this._dispatcher = null;
            this._logHandlers = null;
          },
          _emit: function(type, x) {
            switch (type) {
              case VALUE:
                return this._emitValue(x);
              case ERROR:
                return this._emitError(x);
              case END:
                return this._emitEnd();
            }
          },
          _emitValue: function(value) {
            if (this._alive) {
              this._dispatcher.dispatch({ type: VALUE, value });
            }
          },
          _emitError: function(value) {
            if (this._alive) {
              this._dispatcher.dispatch({ type: ERROR, value });
            }
          },
          _emitEnd: function() {
            if (this._alive) {
              this._alive = false;
              this._dispatcher.dispatch({ type: END });
              this._clear();
            }
          },
          _on: function(type, fn) {
            if (this._alive) {
              this._dispatcher.add(type, fn);
              this._setActive(true);
            } else {
              callSubscriber(type, fn, { type: END });
            }
            return this;
          },
          _off: function(type, fn) {
            if (this._alive) {
              var count = this._dispatcher.remove(type, fn);
              if (count === 0) {
                this._setActive(false);
              }
            }
            return this;
          },
          onValue: function(fn) {
            return this._on(VALUE, fn);
          },
          onError: function(fn) {
            return this._on(ERROR, fn);
          },
          onEnd: function(fn) {
            return this._on(END, fn);
          },
          onAny: function(fn) {
            return this._on(ANY, fn);
          },
          offValue: function(fn) {
            return this._off(VALUE, fn);
          },
          offError: function(fn) {
            return this._off(ERROR, fn);
          },
          offEnd: function(fn) {
            return this._off(END, fn);
          },
          offAny: function(fn) {
            return this._off(ANY, fn);
          },
          observe: function(observerOrOnValue, onError, onEnd) {
            var _this = this;
            var closed = false;
            var observer = !observerOrOnValue || typeof observerOrOnValue === "function" ? { value: observerOrOnValue, error: onError, end: onEnd } : observerOrOnValue;
            var handler = function(event) {
              if (event.type === END) {
                closed = true;
              }
              if (event.type === VALUE && observer.value) {
                observer.value(event.value);
              } else if (event.type === ERROR && observer.error) {
                observer.error(event.value);
              } else if (event.type === END && observer.end) {
                observer.end(event.value);
              }
            };
            this.onAny(handler);
            return {
              unsubscribe: function() {
                if (!closed) {
                  _this.offAny(handler);
                  closed = true;
                }
              },
              get closed() {
                return closed;
              }
            };
          },
          _ofSameType: function(A, B) {
            return A.prototype.getType() === this.getType() ? A : B;
          },
          setName: function(sourceObs, selfName) {
            this._name = selfName ? sourceObs._name + "." + selfName : sourceObs;
            return this;
          },
          log: function() {
            var name = arguments.length > 0 && arguments[0] !== undefined ? arguments[0] : this.toString();
            var isCurrent = undefined;
            var handler = function(event) {
              var type = "<" + event.type + (isCurrent ? ":current" : "") + ">";
              if (event.type === END) {
                console.log(name, type);
              } else {
                console.log(name, type, event.value);
              }
            };
            if (this._alive) {
              if (!this._logHandlers) {
                this._logHandlers = [];
              }
              this._logHandlers.push({ name, handler });
            }
            isCurrent = true;
            this.onAny(handler);
            isCurrent = false;
            return this;
          },
          offLog: function() {
            var name = arguments.length > 0 && arguments[0] !== undefined ? arguments[0] : this.toString();
            if (this._logHandlers) {
              var handlerIndex = findByPred(this._logHandlers, function(obj) {
                return obj.name === name;
              });
              if (handlerIndex !== -1) {
                this.offAny(this._logHandlers[handlerIndex].handler);
                this._logHandlers.splice(handlerIndex, 1);
              }
            }
            return this;
          },
          spy: function() {
            var name = arguments.length > 0 && arguments[0] !== undefined ? arguments[0] : this.toString();
            var handler = function(event) {
              var type = "<" + event.type + ">";
              if (event.type === END) {
                console.log(name, type);
              } else {
                console.log(name, type, event.value);
              }
            };
            if (this._alive) {
              if (!this._spyHandlers) {
                this._spyHandlers = [];
              }
              this._spyHandlers.push({ name, handler });
              this._dispatcher.addSpy(handler);
            }
            return this;
          },
          offSpy: function() {
            var name = arguments.length > 0 && arguments[0] !== undefined ? arguments[0] : this.toString();
            if (this._spyHandlers) {
              var handlerIndex = findByPred(this._spyHandlers, function(obj) {
                return obj.name === name;
              });
              if (handlerIndex !== -1) {
                this._dispatcher.removeSpy(this._spyHandlers[handlerIndex].handler);
                this._spyHandlers.splice(handlerIndex, 1);
              }
            }
            return this;
          }
        });
        Observable.prototype.toString = function() {
          return "[" + this._name + "]";
        };
        function Stream() {
          Observable.call(this);
        }
        inherit(Stream, Observable, {
          _name: "stream",
          getType: function() {
            return "stream";
          }
        });
        function Property() {
          Observable.call(this);
          this._currentEvent = null;
        }
        inherit(Property, Observable, {
          _name: "property",
          _emitValue: function(value) {
            if (this._alive) {
              this._currentEvent = { type: VALUE, value };
              if (!this._activating) {
                this._dispatcher.dispatch({ type: VALUE, value });
              }
            }
          },
          _emitError: function(value) {
            if (this._alive) {
              this._currentEvent = { type: ERROR, value };
              if (!this._activating) {
                this._dispatcher.dispatch({ type: ERROR, value });
              }
            }
          },
          _emitEnd: function() {
            if (this._alive) {
              this._alive = false;
              if (!this._activating) {
                this._dispatcher.dispatch({ type: END });
              }
              this._clear();
            }
          },
          _on: function(type, fn) {
            if (this._alive) {
              this._dispatcher.add(type, fn);
              this._setActive(true);
            }
            if (this._currentEvent !== null) {
              callSubscriber(type, fn, this._currentEvent);
            }
            if (!this._alive) {
              callSubscriber(type, fn, { type: END });
            }
            return this;
          },
          getType: function() {
            return "property";
          }
        });
        var neverS = new Stream;
        neverS._emitEnd();
        neverS._name = "never";
        function never() {
          return neverS;
        }
        function timeBased(mixin2) {
          function AnonymousStream(wait, options) {
            var _this = this;
            Stream.call(this);
            this._wait = wait;
            this._intervalId = null;
            this._$onTick = function() {
              return _this._onTick();
            };
            this._init(options);
          }
          inherit(AnonymousStream, Stream, {
            _init: function() {},
            _free: function() {},
            _onTick: function() {},
            _onActivation: function() {
              this._intervalId = setInterval(this._$onTick, this._wait);
            },
            _onDeactivation: function() {
              if (this._intervalId !== null) {
                clearInterval(this._intervalId);
                this._intervalId = null;
              }
            },
            _clear: function() {
              Stream.prototype._clear.call(this);
              this._$onTick = null;
              this._free();
            }
          }, mixin2);
          return AnonymousStream;
        }
        var S = timeBased({
          _name: "later",
          _init: function(_ref) {
            var x = _ref.x;
            this._x = x;
          },
          _free: function() {
            this._x = null;
          },
          _onTick: function() {
            this._emitValue(this._x);
            this._emitEnd();
          }
        });
        function later(wait, x) {
          return new S(wait, { x });
        }
        var S$1 = timeBased({
          _name: "interval",
          _init: function(_ref) {
            var x = _ref.x;
            this._x = x;
          },
          _free: function() {
            this._x = null;
          },
          _onTick: function() {
            this._emitValue(this._x);
          }
        });
        function interval(wait, x) {
          return new S$1(wait, { x });
        }
        var S$2 = timeBased({
          _name: "sequentially",
          _init: function(_ref) {
            var xs = _ref.xs;
            this._xs = cloneArray(xs);
          },
          _free: function() {
            this._xs = null;
          },
          _onTick: function() {
            if (this._xs.length === 1) {
              this._emitValue(this._xs[0]);
              this._emitEnd();
            } else {
              this._emitValue(this._xs.shift());
            }
          }
        });
        function sequentially(wait, xs) {
          return xs.length === 0 ? never() : new S$2(wait, { xs });
        }
        var S$3 = timeBased({
          _name: "fromPoll",
          _init: function(_ref) {
            var fn = _ref.fn;
            this._fn = fn;
          },
          _free: function() {
            this._fn = null;
          },
          _onTick: function() {
            var fn = this._fn;
            this._emitValue(fn());
          }
        });
        function fromPoll(wait, fn) {
          return new S$3(wait, { fn });
        }
        function emitter(obs) {
          function value(x) {
            obs._emitValue(x);
            return obs._active;
          }
          function error(x) {
            obs._emitError(x);
            return obs._active;
          }
          function end() {
            obs._emitEnd();
            return obs._active;
          }
          function event(e) {
            obs._emit(e.type, e.value);
            return obs._active;
          }
          return {
            value,
            error,
            end,
            event,
            emit: value,
            emitEvent: event
          };
        }
        var S$4 = timeBased({
          _name: "withInterval",
          _init: function(_ref) {
            var fn = _ref.fn;
            this._fn = fn;
            this._emitter = emitter(this);
          },
          _free: function() {
            this._fn = null;
            this._emitter = null;
          },
          _onTick: function() {
            var fn = this._fn;
            fn(this._emitter);
          }
        });
        function withInterval(wait, fn) {
          return new S$4(wait, { fn });
        }
        function S$5(fn) {
          Stream.call(this);
          this._fn = fn;
          this._unsubscribe = null;
        }
        inherit(S$5, Stream, {
          _name: "stream",
          _onActivation: function() {
            var fn = this._fn;
            var unsubscribe = fn(emitter(this));
            this._unsubscribe = typeof unsubscribe === "function" ? unsubscribe : null;
            if (!this._active) {
              this._callUnsubscribe();
            }
          },
          _callUnsubscribe: function() {
            if (this._unsubscribe !== null) {
              this._unsubscribe();
              this._unsubscribe = null;
            }
          },
          _onDeactivation: function() {
            this._callUnsubscribe();
          },
          _clear: function() {
            Stream.prototype._clear.call(this);
            this._fn = null;
          }
        });
        function stream(fn) {
          return new S$5(fn);
        }
        function fromCallback(callbackConsumer) {
          var called = false;
          return stream(function(emitter2) {
            if (!called) {
              callbackConsumer(function(x) {
                emitter2.emit(x);
                emitter2.end();
              });
              called = true;
            }
          }).setName("fromCallback");
        }
        function fromNodeCallback(callbackConsumer) {
          var called = false;
          return stream(function(emitter2) {
            if (!called) {
              callbackConsumer(function(error, x) {
                if (error) {
                  emitter2.error(error);
                } else {
                  emitter2.emit(x);
                }
                emitter2.end();
              });
              called = true;
            }
          }).setName("fromNodeCallback");
        }
        function spread(fn, length) {
          switch (length) {
            case 0:
              return function() {
                return fn();
              };
            case 1:
              return function(a) {
                return fn(a[0]);
              };
            case 2:
              return function(a) {
                return fn(a[0], a[1]);
              };
            case 3:
              return function(a) {
                return fn(a[0], a[1], a[2]);
              };
            case 4:
              return function(a) {
                return fn(a[0], a[1], a[2], a[3]);
              };
            default:
              return function(a) {
                return fn.apply(null, a);
              };
          }
        }
        function apply(fn, c, a) {
          var aLength = a ? a.length : 0;
          if (c == null) {
            switch (aLength) {
              case 0:
                return fn();
              case 1:
                return fn(a[0]);
              case 2:
                return fn(a[0], a[1]);
              case 3:
                return fn(a[0], a[1], a[2]);
              case 4:
                return fn(a[0], a[1], a[2], a[3]);
              default:
                return fn.apply(null, a);
            }
          } else {
            switch (aLength) {
              case 0:
                return fn.call(c);
              default:
                return fn.apply(c, a);
            }
          }
        }
        function fromSubUnsub(sub, unsub, transformer) {
          return stream(function(emitter2) {
            var handler = transformer ? function() {
              emitter2.emit(apply(transformer, this, arguments));
            } : function(x) {
              emitter2.emit(x);
            };
            sub(handler);
            return function() {
              return unsub(handler);
            };
          }).setName("fromSubUnsub");
        }
        var pairs = [["addEventListener", "removeEventListener"], ["addListener", "removeListener"], ["on", "off"]];
        function fromEvents(target, eventName, transformer) {
          var sub = undefined, unsub = undefined;
          for (var i = 0;i < pairs.length; i++) {
            if (typeof target[pairs[i][0]] === "function" && typeof target[pairs[i][1]] === "function") {
              sub = pairs[i][0];
              unsub = pairs[i][1];
              break;
            }
          }
          if (sub === undefined) {
            throw new Error("target don't support any of " + "addEventListener/removeEventListener, addListener/removeListener, on/off method pair");
          }
          return fromSubUnsub(function(handler) {
            return target[sub](eventName, handler);
          }, function(handler) {
            return target[unsub](eventName, handler);
          }, transformer).setName("fromEvents");
        }
        function P(value) {
          this._currentEvent = { type: "value", value, current: true };
        }
        inherit(P, Property, {
          _name: "constant",
          _active: false,
          _activating: false,
          _alive: false,
          _dispatcher: null,
          _logHandlers: null
        });
        function constant(x) {
          return new P(x);
        }
        function P$1(value) {
          this._currentEvent = { type: "error", value, current: true };
        }
        inherit(P$1, Property, {
          _name: "constantError",
          _active: false,
          _activating: false,
          _alive: false,
          _dispatcher: null,
          _logHandlers: null
        });
        function constantError(x) {
          return new P$1(x);
        }
        function createConstructor(BaseClass, name) {
          return function AnonymousObservable(source, options) {
            var _this = this;
            BaseClass.call(this);
            this._source = source;
            this._name = source._name + "." + name;
            this._init(options);
            this._$handleAny = function(event) {
              return _this._handleAny(event);
            };
          };
        }
        function createClassMethods(BaseClass) {
          return {
            _init: function() {},
            _free: function() {},
            _handleValue: function(x) {
              this._emitValue(x);
            },
            _handleError: function(x) {
              this._emitError(x);
            },
            _handleEnd: function() {
              this._emitEnd();
            },
            _handleAny: function(event) {
              switch (event.type) {
                case VALUE:
                  return this._handleValue(event.value);
                case ERROR:
                  return this._handleError(event.value);
                case END:
                  return this._handleEnd();
              }
            },
            _onActivation: function() {
              this._source.onAny(this._$handleAny);
            },
            _onDeactivation: function() {
              this._source.offAny(this._$handleAny);
            },
            _clear: function() {
              BaseClass.prototype._clear.call(this);
              this._source = null;
              this._$handleAny = null;
              this._free();
            }
          };
        }
        function createStream(name, mixin2) {
          var S2 = createConstructor(Stream, name);
          inherit(S2, Stream, createClassMethods(Stream), mixin2);
          return S2;
        }
        function createProperty(name, mixin2) {
          var P2 = createConstructor(Property, name);
          inherit(P2, Property, createClassMethods(Property), mixin2);
          return P2;
        }
        var P$2 = createProperty("toProperty", {
          _init: function(_ref) {
            var fn = _ref.fn;
            this._getInitialCurrent = fn;
          },
          _onActivation: function() {
            if (this._getInitialCurrent !== null) {
              var getInitial = this._getInitialCurrent;
              this._emitValue(getInitial());
            }
            this._source.onAny(this._$handleAny);
          }
        });
        function toProperty(obs) {
          var fn = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : null;
          if (fn !== null && typeof fn !== "function") {
            throw new Error("You should call toProperty() with a function or no arguments.");
          }
          return new P$2(obs, { fn });
        }
        var S$6 = createStream("changes", {
          _handleValue: function(x) {
            if (!this._activating) {
              this._emitValue(x);
            }
          },
          _handleError: function(x) {
            if (!this._activating) {
              this._emitError(x);
            }
          }
        });
        function changes(obs) {
          return new S$6(obs);
        }
        function fromPromise(promise) {
          var called = false;
          var result2 = stream(function(emitter2) {
            if (!called) {
              var onValue = function(x) {
                emitter2.emit(x);
                emitter2.end();
              };
              var onError = function(x) {
                emitter2.error(x);
                emitter2.end();
              };
              var _promise = promise.then(onValue, onError);
              if (_promise && typeof _promise.done === "function") {
                _promise.done();
              }
              called = true;
            }
          });
          return toProperty(result2, null).setName("fromPromise");
        }
        function getGlodalPromise() {
          if (typeof Promise === "function") {
            return Promise;
          } else {
            throw new Error("There isn't default Promise, use shim or parameter");
          }
        }
        var toPromise = function(obs) {
          var Promise2 = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : getGlodalPromise();
          var last3 = null;
          return new Promise2(function(resolve, reject) {
            obs.onAny(function(event) {
              if (event.type === END && last3 !== null) {
                (last3.type === VALUE ? resolve : reject)(last3.value);
                last3 = null;
              } else {
                last3 = event;
              }
            });
          });
        };
        function symbolObservablePonyfill(root2) {
          var result2;
          var Symbol2 = root2.Symbol;
          if (typeof Symbol2 === "function") {
            if (Symbol2.observable) {
              result2 = Symbol2.observable;
            } else {
              result2 = Symbol2("observable");
              Symbol2.observable = result2;
            }
          } else {
            result2 = "@@observable";
          }
          return result2;
        }
        var root;
        if (typeof self !== "undefined") {
          root = self;
        } else if (typeof window !== "undefined") {
          root = window;
        } else if (typeof __webpack_require__2.g !== "undefined") {
          root = __webpack_require__2.g;
        } else if (true) {
          root = module;
        } else {}
        var result = symbolObservablePonyfill(root);
        var $$observable = result.default ? result.default : result;
        function fromESObservable(_observable) {
          var observable = _observable[$$observable] ? _observable[$$observable]() : _observable;
          return stream(function(emitter2) {
            var unsub = observable.subscribe({
              error: function(error) {
                emitter2.error(error);
                emitter2.end();
              },
              next: function(value) {
                emitter2.emit(value);
              },
              complete: function() {
                emitter2.end();
              }
            });
            if (unsub.unsubscribe) {
              return function() {
                unsub.unsubscribe();
              };
            } else {
              return unsub;
            }
          }).setName("fromESObservable");
        }
        function ESObservable(observable) {
          this._observable = observable.takeErrors(1);
        }
        extend(ESObservable.prototype, {
          subscribe: function(observerOrOnNext, onError, onComplete) {
            var _this = this;
            var observer = typeof observerOrOnNext === "function" ? { next: observerOrOnNext, error: onError, complete: onComplete } : observerOrOnNext;
            var fn = function(event) {
              if (event.type === END) {
                closed = true;
              }
              if (event.type === VALUE && observer.next) {
                observer.next(event.value);
              } else if (event.type === ERROR && observer.error) {
                observer.error(event.value);
              } else if (event.type === END && observer.complete) {
                observer.complete(event.value);
              }
            };
            this._observable.onAny(fn);
            var closed = false;
            var subscription = {
              unsubscribe: function() {
                closed = true;
                _this._observable.offAny(fn);
              },
              get closed() {
                return closed;
              }
            };
            return subscription;
          }
        });
        ESObservable.prototype[$$observable] = function() {
          return this;
        };
        function toESObservable() {
          return new ESObservable(this);
        }
        function collect(source, keys, values) {
          for (var prop in source) {
            if (source.hasOwnProperty(prop)) {
              keys.push(prop);
              values.push(source[prop]);
            }
          }
        }
        function defaultErrorsCombinator(errors) {
          var latestError = undefined;
          for (var i = 0;i < errors.length; i++) {
            if (errors[i] !== undefined) {
              if (latestError === undefined || latestError.index < errors[i].index) {
                latestError = errors[i];
              }
            }
          }
          return latestError.error;
        }
        function Combine(active, passive, combinator) {
          var _this = this;
          Stream.call(this);
          this._activeCount = active.length;
          this._sources = concat(active, passive);
          this._combinator = combinator;
          this._aliveCount = 0;
          this._latestValues = new Array(this._sources.length);
          this._latestErrors = new Array(this._sources.length);
          fillArray(this._latestValues, NOTHING);
          this._emitAfterActivation = false;
          this._endAfterActivation = false;
          this._latestErrorIndex = 0;
          this._$handlers = [];
          var _loop = function(i2) {
            _this._$handlers.push(function(event) {
              return _this._handleAny(i2, event);
            });
          };
          for (var i = 0;i < this._sources.length; i++) {
            _loop(i);
          }
        }
        inherit(Combine, Stream, {
          _name: "combine",
          _onActivation: function() {
            this._aliveCount = this._activeCount;
            for (var i = this._activeCount;i < this._sources.length; i++) {
              this._sources[i].onAny(this._$handlers[i]);
            }
            for (var _i = 0;_i < this._activeCount; _i++) {
              this._sources[_i].onAny(this._$handlers[_i]);
            }
            if (this._emitAfterActivation) {
              this._emitAfterActivation = false;
              this._emitIfFull();
            }
            if (this._endAfterActivation) {
              this._emitEnd();
            }
          },
          _onDeactivation: function() {
            var length = this._sources.length, i = undefined;
            for (i = 0;i < length; i++) {
              this._sources[i].offAny(this._$handlers[i]);
            }
          },
          _emitIfFull: function() {
            var hasAllValues = true;
            var hasErrors = false;
            var length = this._latestValues.length;
            var valuesCopy = new Array(length);
            var errorsCopy = new Array(length);
            for (var i = 0;i < length; i++) {
              valuesCopy[i] = this._latestValues[i];
              errorsCopy[i] = this._latestErrors[i];
              if (valuesCopy[i] === NOTHING) {
                hasAllValues = false;
              }
              if (errorsCopy[i] !== undefined) {
                hasErrors = true;
              }
            }
            if (hasAllValues) {
              var combinator = this._combinator;
              this._emitValue(combinator(valuesCopy));
            }
            if (hasErrors) {
              this._emitError(defaultErrorsCombinator(errorsCopy));
            }
          },
          _handleAny: function(i, event) {
            if (event.type === VALUE || event.type === ERROR) {
              if (event.type === VALUE) {
                this._latestValues[i] = event.value;
                this._latestErrors[i] = undefined;
              }
              if (event.type === ERROR) {
                this._latestValues[i] = NOTHING;
                this._latestErrors[i] = {
                  index: this._latestErrorIndex++,
                  error: event.value
                };
              }
              if (i < this._activeCount) {
                if (this._activating) {
                  this._emitAfterActivation = true;
                } else {
                  this._emitIfFull();
                }
              }
            } else {
              if (i < this._activeCount) {
                this._aliveCount--;
                if (this._aliveCount === 0) {
                  if (this._activating) {
                    this._endAfterActivation = true;
                  } else {
                    this._emitEnd();
                  }
                }
              }
            }
          },
          _clear: function() {
            Stream.prototype._clear.call(this);
            this._sources = null;
            this._latestValues = null;
            this._latestErrors = null;
            this._combinator = null;
            this._$handlers = null;
          }
        });
        function combineAsArray(active) {
          var passive = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : [];
          var combinator = arguments[2];
          if (!Array.isArray(passive)) {
            throw new Error("Combine can only combine active and passive collections of the same type.");
          }
          combinator = combinator ? spread(combinator, active.length + passive.length) : function(x) {
            return x;
          };
          return active.length === 0 ? never() : new Combine(active, passive, combinator);
        }
        function combineAsObject(active) {
          var passive = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : {};
          var combinator = arguments[2];
          if (typeof passive !== "object" || Array.isArray(passive)) {
            throw new Error("Combine can only combine active and passive collections of the same type.");
          }
          var keys = [], activeObservables = [], passiveObservables = [];
          collect(active, keys, activeObservables);
          collect(passive, keys, passiveObservables);
          var objectify = function(values) {
            var event = {};
            for (var i = values.length - 1;0 <= i; i--) {
              event[keys[i]] = values[i];
            }
            return combinator ? combinator(event) : event;
          };
          return activeObservables.length === 0 ? never() : new Combine(activeObservables, passiveObservables, objectify);
        }
        function combine(active, passive, combinator) {
          if (typeof passive === "function") {
            combinator = passive;
            passive = undefined;
          }
          return Array.isArray(active) ? combineAsArray(active, passive, combinator) : combineAsObject(active, passive, combinator);
        }
        var Observable$2 = {
          empty: function() {
            return never();
          },
          concat: function(a, b) {
            return a.merge(b);
          },
          of: function(x) {
            return constant(x);
          },
          map: function(fn, obs) {
            return obs.map(fn);
          },
          bimap: function(fnErr, fnVal, obs) {
            return obs.mapErrors(fnErr).map(fnVal);
          },
          ap: function(obsFn, obsVal) {
            return combine([obsFn, obsVal], function(fn, val) {
              return fn(val);
            });
          },
          chain: function(fn, obs) {
            return obs.flatMap(fn);
          }
        };
        var staticLand = Object.freeze({
          Observable: Observable$2
        });
        var mixin = {
          _init: function(_ref) {
            var fn = _ref.fn;
            this._fn = fn;
          },
          _free: function() {
            this._fn = null;
          },
          _handleValue: function(x) {
            var fn = this._fn;
            this._emitValue(fn(x));
          }
        };
        var S$7 = createStream("map", mixin);
        var P$3 = createProperty("map", mixin);
        var id = function(x) {
          return x;
        };
        function map$1(obs) {
          var fn = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : id;
          return new (obs._ofSameType(S$7, P$3))(obs, { fn });
        }
        var mixin$1 = {
          _init: function(_ref) {
            var fn = _ref.fn;
            this._fn = fn;
          },
          _free: function() {
            this._fn = null;
          },
          _handleValue: function(x) {
            var fn = this._fn;
            if (fn(x)) {
              this._emitValue(x);
            }
          }
        };
        var S$8 = createStream("filter", mixin$1);
        var P$4 = createProperty("filter", mixin$1);
        var id$1 = function(x) {
          return x;
        };
        function filter(obs) {
          var fn = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : id$1;
          return new (obs._ofSameType(S$8, P$4))(obs, { fn });
        }
        var mixin$2 = {
          _init: function(_ref) {
            var n = _ref.n;
            this._n = n;
            if (n <= 0) {
              this._emitEnd();
            }
          },
          _handleValue: function(x) {
            if (this._n === 0) {
              return;
            }
            this._n--;
            this._emitValue(x);
            if (this._n === 0) {
              this._emitEnd();
            }
          }
        };
        var S$9 = createStream("take", mixin$2);
        var P$5 = createProperty("take", mixin$2);
        function take(obs, n) {
          return new (obs._ofSameType(S$9, P$5))(obs, { n });
        }
        var mixin$3 = {
          _init: function(_ref) {
            var n = _ref.n;
            this._n = n;
            if (n <= 0) {
              this._emitEnd();
            }
          },
          _handleError: function(x) {
            if (this._n === 0) {
              return;
            }
            this._n--;
            this._emitError(x);
            if (this._n === 0) {
              this._emitEnd();
            }
          }
        };
        var S$10 = createStream("takeErrors", mixin$3);
        var P$6 = createProperty("takeErrors", mixin$3);
        function takeErrors(obs, n) {
          return new (obs._ofSameType(S$10, P$6))(obs, { n });
        }
        var mixin$4 = {
          _init: function(_ref) {
            var fn = _ref.fn;
            this._fn = fn;
          },
          _free: function() {
            this._fn = null;
          },
          _handleValue: function(x) {
            var fn = this._fn;
            if (fn(x)) {
              this._emitValue(x);
            } else {
              this._emitEnd();
            }
          }
        };
        var S$11 = createStream("takeWhile", mixin$4);
        var P$7 = createProperty("takeWhile", mixin$4);
        var id$2 = function(x) {
          return x;
        };
        function takeWhile(obs) {
          var fn = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : id$2;
          return new (obs._ofSameType(S$11, P$7))(obs, { fn });
        }
        var mixin$5 = {
          _init: function() {
            this._lastValue = NOTHING;
          },
          _free: function() {
            this._lastValue = null;
          },
          _handleValue: function(x) {
            this._lastValue = x;
          },
          _handleEnd: function() {
            if (this._lastValue !== NOTHING) {
              this._emitValue(this._lastValue);
            }
            this._emitEnd();
          }
        };
        var S$12 = createStream("last", mixin$5);
        var P$8 = createProperty("last", mixin$5);
        function last2(obs) {
          return new (obs._ofSameType(S$12, P$8))(obs);
        }
        var mixin$6 = {
          _init: function(_ref) {
            var n = _ref.n;
            this._n = Math.max(0, n);
          },
          _handleValue: function(x) {
            if (this._n === 0) {
              this._emitValue(x);
            } else {
              this._n--;
            }
          }
        };
        var S$13 = createStream("skip", mixin$6);
        var P$9 = createProperty("skip", mixin$6);
        function skip(obs, n) {
          return new (obs._ofSameType(S$13, P$9))(obs, { n });
        }
        var mixin$7 = {
          _init: function(_ref) {
            var fn = _ref.fn;
            this._fn = fn;
          },
          _free: function() {
            this._fn = null;
          },
          _handleValue: function(x) {
            var fn = this._fn;
            if (this._fn !== null && !fn(x)) {
              this._fn = null;
            }
            if (this._fn === null) {
              this._emitValue(x);
            }
          }
        };
        var S$14 = createStream("skipWhile", mixin$7);
        var P$10 = createProperty("skipWhile", mixin$7);
        var id$3 = function(x) {
          return x;
        };
        function skipWhile(obs) {
          var fn = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : id$3;
          return new (obs._ofSameType(S$14, P$10))(obs, { fn });
        }
        var mixin$8 = {
          _init: function(_ref) {
            var fn = _ref.fn;
            this._fn = fn;
            this._prev = NOTHING;
          },
          _free: function() {
            this._fn = null;
            this._prev = null;
          },
          _handleValue: function(x) {
            var fn = this._fn;
            if (this._prev === NOTHING || !fn(this._prev, x)) {
              this._prev = x;
              this._emitValue(x);
            }
          }
        };
        var S$15 = createStream("skipDuplicates", mixin$8);
        var P$11 = createProperty("skipDuplicates", mixin$8);
        var eq = function(a, b) {
          return a === b;
        };
        function skipDuplicates(obs) {
          var fn = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : eq;
          return new (obs._ofSameType(S$15, P$11))(obs, { fn });
        }
        var mixin$9 = {
          _init: function(_ref) {
            var { fn, seed } = _ref;
            this._fn = fn;
            this._prev = seed;
          },
          _free: function() {
            this._prev = null;
            this._fn = null;
          },
          _handleValue: function(x) {
            if (this._prev !== NOTHING) {
              var fn = this._fn;
              this._emitValue(fn(this._prev, x));
            }
            this._prev = x;
          }
        };
        var S$16 = createStream("diff", mixin$9);
        var P$12 = createProperty("diff", mixin$9);
        function defaultFn(a, b) {
          return [a, b];
        }
        function diff(obs, fn) {
          var seed = arguments.length > 2 && arguments[2] !== undefined ? arguments[2] : NOTHING;
          return new (obs._ofSameType(S$16, P$12))(obs, { fn: fn || defaultFn, seed });
        }
        var P$13 = createProperty("scan", {
          _init: function(_ref) {
            var { fn, seed } = _ref;
            this._fn = fn;
            this._seed = seed;
            if (seed !== NOTHING) {
              this._emitValue(seed);
            }
          },
          _free: function() {
            this._fn = null;
            this._seed = null;
          },
          _handleValue: function(x) {
            var fn = this._fn;
            if (this._currentEvent === null || this._currentEvent.type === ERROR) {
              this._emitValue(this._seed === NOTHING ? x : fn(this._seed, x));
            } else {
              this._emitValue(fn(this._currentEvent.value, x));
            }
          }
        });
        function scan(obs, fn) {
          var seed = arguments.length > 2 && arguments[2] !== undefined ? arguments[2] : NOTHING;
          return new P$13(obs, { fn, seed });
        }
        var mixin$10 = {
          _init: function(_ref) {
            var fn = _ref.fn;
            this._fn = fn;
          },
          _free: function() {
            this._fn = null;
          },
          _handleValue: function(x) {
            var fn = this._fn;
            var xs = fn(x);
            for (var i = 0;i < xs.length; i++) {
              this._emitValue(xs[i]);
            }
          }
        };
        var S$17 = createStream("flatten", mixin$10);
        var id$4 = function(x) {
          return x;
        };
        function flatten2(obs) {
          var fn = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : id$4;
          return new S$17(obs, { fn });
        }
        var END_MARKER = {};
        var mixin$11 = {
          _init: function(_ref) {
            var _this = this;
            var wait = _ref.wait;
            this._wait = Math.max(0, wait);
            this._buff = [];
            this._$shiftBuff = function() {
              var value = _this._buff.shift();
              if (value === END_MARKER) {
                _this._emitEnd();
              } else {
                _this._emitValue(value);
              }
            };
          },
          _free: function() {
            this._buff = null;
            this._$shiftBuff = null;
          },
          _handleValue: function(x) {
            if (this._activating) {
              this._emitValue(x);
            } else {
              this._buff.push(x);
              setTimeout(this._$shiftBuff, this._wait);
            }
          },
          _handleEnd: function() {
            if (this._activating) {
              this._emitEnd();
            } else {
              this._buff.push(END_MARKER);
              setTimeout(this._$shiftBuff, this._wait);
            }
          }
        };
        var S$18 = createStream("delay", mixin$11);
        var P$14 = createProperty("delay", mixin$11);
        function delay(obs, wait) {
          return new (obs._ofSameType(S$18, P$14))(obs, { wait });
        }
        var now = Date.now ? function() {
          return Date.now();
        } : function() {
          return new Date().getTime();
        };
        var mixin$12 = {
          _init: function(_ref) {
            var _this = this;
            var { wait, leading, trailing } = _ref;
            this._wait = Math.max(0, wait);
            this._leading = leading;
            this._trailing = trailing;
            this._trailingValue = null;
            this._timeoutId = null;
            this._endLater = false;
            this._lastCallTime = 0;
            this._$trailingCall = function() {
              return _this._trailingCall();
            };
          },
          _free: function() {
            this._trailingValue = null;
            this._$trailingCall = null;
          },
          _handleValue: function(x) {
            if (this._activating) {
              this._emitValue(x);
            } else {
              var curTime = now();
              if (this._lastCallTime === 0 && !this._leading) {
                this._lastCallTime = curTime;
              }
              var remaining = this._wait - (curTime - this._lastCallTime);
              if (remaining <= 0) {
                this._cancelTrailing();
                this._lastCallTime = curTime;
                this._emitValue(x);
              } else if (this._trailing) {
                this._cancelTrailing();
                this._trailingValue = x;
                this._timeoutId = setTimeout(this._$trailingCall, remaining);
              }
            }
          },
          _handleEnd: function() {
            if (this._activating) {
              this._emitEnd();
            } else {
              if (this._timeoutId) {
                this._endLater = true;
              } else {
                this._emitEnd();
              }
            }
          },
          _cancelTrailing: function() {
            if (this._timeoutId !== null) {
              clearTimeout(this._timeoutId);
              this._timeoutId = null;
            }
          },
          _trailingCall: function() {
            this._emitValue(this._trailingValue);
            this._timeoutId = null;
            this._trailingValue = null;
            this._lastCallTime = !this._leading ? 0 : now();
            if (this._endLater) {
              this._emitEnd();
            }
          }
        };
        var S$19 = createStream("throttle", mixin$12);
        var P$15 = createProperty("throttle", mixin$12);
        function throttle(obs, wait) {
          var _ref2 = arguments.length > 2 && arguments[2] !== undefined ? arguments[2] : {}, _ref2$leading = _ref2.leading, leading = _ref2$leading === undefined ? true : _ref2$leading, _ref2$trailing = _ref2.trailing, trailing = _ref2$trailing === undefined ? true : _ref2$trailing;
          return new (obs._ofSameType(S$19, P$15))(obs, { wait, leading, trailing });
        }
        var mixin$13 = {
          _init: function(_ref) {
            var _this = this;
            var { wait, immediate } = _ref;
            this._wait = Math.max(0, wait);
            this._immediate = immediate;
            this._lastAttempt = 0;
            this._timeoutId = null;
            this._laterValue = null;
            this._endLater = false;
            this._$later = function() {
              return _this._later();
            };
          },
          _free: function() {
            this._laterValue = null;
            this._$later = null;
          },
          _handleValue: function(x) {
            if (this._activating) {
              this._emitValue(x);
            } else {
              this._lastAttempt = now();
              if (this._immediate && !this._timeoutId) {
                this._emitValue(x);
              }
              if (!this._timeoutId) {
                this._timeoutId = setTimeout(this._$later, this._wait);
              }
              if (!this._immediate) {
                this._laterValue = x;
              }
            }
          },
          _handleEnd: function() {
            if (this._activating) {
              this._emitEnd();
            } else {
              if (this._timeoutId && !this._immediate) {
                this._endLater = true;
              } else {
                this._emitEnd();
              }
            }
          },
          _later: function() {
            var last3 = now() - this._lastAttempt;
            if (last3 < this._wait && last3 >= 0) {
              this._timeoutId = setTimeout(this._$later, this._wait - last3);
            } else {
              this._timeoutId = null;
              if (!this._immediate) {
                var _laterValue = this._laterValue;
                this._laterValue = null;
                this._emitValue(_laterValue);
              }
              if (this._endLater) {
                this._emitEnd();
              }
            }
          }
        };
        var S$20 = createStream("debounce", mixin$13);
        var P$16 = createProperty("debounce", mixin$13);
        function debounce(obs, wait) {
          var _ref2 = arguments.length > 2 && arguments[2] !== undefined ? arguments[2] : {}, _ref2$immediate = _ref2.immediate, immediate = _ref2$immediate === undefined ? false : _ref2$immediate;
          return new (obs._ofSameType(S$20, P$16))(obs, { wait, immediate });
        }
        var mixin$14 = {
          _init: function(_ref) {
            var fn = _ref.fn;
            this._fn = fn;
          },
          _free: function() {
            this._fn = null;
          },
          _handleError: function(x) {
            var fn = this._fn;
            this._emitError(fn(x));
          }
        };
        var S$21 = createStream("mapErrors", mixin$14);
        var P$17 = createProperty("mapErrors", mixin$14);
        var id$5 = function(x) {
          return x;
        };
        function mapErrors(obs) {
          var fn = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : id$5;
          return new (obs._ofSameType(S$21, P$17))(obs, { fn });
        }
        var mixin$15 = {
          _init: function(_ref) {
            var fn = _ref.fn;
            this._fn = fn;
          },
          _free: function() {
            this._fn = null;
          },
          _handleError: function(x) {
            var fn = this._fn;
            if (fn(x)) {
              this._emitError(x);
            }
          }
        };
        var S$22 = createStream("filterErrors", mixin$15);
        var P$18 = createProperty("filterErrors", mixin$15);
        var id$6 = function(x) {
          return x;
        };
        function filterErrors(obs) {
          var fn = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : id$6;
          return new (obs._ofSameType(S$22, P$18))(obs, { fn });
        }
        var mixin$16 = {
          _handleValue: function() {}
        };
        var S$23 = createStream("ignoreValues", mixin$16);
        var P$19 = createProperty("ignoreValues", mixin$16);
        function ignoreValues(obs) {
          return new (obs._ofSameType(S$23, P$19))(obs);
        }
        var mixin$17 = {
          _handleError: function() {}
        };
        var S$24 = createStream("ignoreErrors", mixin$17);
        var P$20 = createProperty("ignoreErrors", mixin$17);
        function ignoreErrors(obs) {
          return new (obs._ofSameType(S$24, P$20))(obs);
        }
        var mixin$18 = {
          _handleEnd: function() {}
        };
        var S$25 = createStream("ignoreEnd", mixin$18);
        var P$21 = createProperty("ignoreEnd", mixin$18);
        function ignoreEnd(obs) {
          return new (obs._ofSameType(S$25, P$21))(obs);
        }
        var mixin$19 = {
          _init: function(_ref) {
            var fn = _ref.fn;
            this._fn = fn;
          },
          _free: function() {
            this._fn = null;
          },
          _handleEnd: function() {
            var fn = this._fn;
            this._emitValue(fn());
            this._emitEnd();
          }
        };
        var S$26 = createStream("beforeEnd", mixin$19);
        var P$22 = createProperty("beforeEnd", mixin$19);
        function beforeEnd(obs, fn) {
          return new (obs._ofSameType(S$26, P$22))(obs, { fn });
        }
        var mixin$20 = {
          _init: function(_ref) {
            var { min, max } = _ref;
            this._max = max;
            this._min = min;
            this._buff = [];
          },
          _free: function() {
            this._buff = null;
          },
          _handleValue: function(x) {
            this._buff = slide(this._buff, x, this._max);
            if (this._buff.length >= this._min) {
              this._emitValue(this._buff);
            }
          }
        };
        var S$27 = createStream("slidingWindow", mixin$20);
        var P$23 = createProperty("slidingWindow", mixin$20);
        function slidingWindow(obs, max) {
          var min = arguments.length > 2 && arguments[2] !== undefined ? arguments[2] : 0;
          return new (obs._ofSameType(S$27, P$23))(obs, { min, max });
        }
        var mixin$21 = {
          _init: function(_ref) {
            var { fn, flushOnEnd } = _ref;
            this._fn = fn;
            this._flushOnEnd = flushOnEnd;
            this._buff = [];
          },
          _free: function() {
            this._buff = null;
          },
          _flush: function() {
            if (this._buff !== null && this._buff.length !== 0) {
              this._emitValue(this._buff);
              this._buff = [];
            }
          },
          _handleValue: function(x) {
            this._buff.push(x);
            var fn = this._fn;
            if (!fn(x)) {
              this._flush();
            }
          },
          _handleEnd: function() {
            if (this._flushOnEnd) {
              this._flush();
            }
            this._emitEnd();
          }
        };
        var S$28 = createStream("bufferWhile", mixin$21);
        var P$24 = createProperty("bufferWhile", mixin$21);
        var id$7 = function(x) {
          return x;
        };
        function bufferWhile(obs, fn) {
          var _ref2 = arguments.length > 2 && arguments[2] !== undefined ? arguments[2] : {}, _ref2$flushOnEnd = _ref2.flushOnEnd, flushOnEnd = _ref2$flushOnEnd === undefined ? true : _ref2$flushOnEnd;
          return new (obs._ofSameType(S$28, P$24))(obs, { fn: fn || id$7, flushOnEnd });
        }
        var mixin$22 = {
          _init: function(_ref) {
            var { count, flushOnEnd } = _ref;
            this._count = count;
            this._flushOnEnd = flushOnEnd;
            this._buff = [];
          },
          _free: function() {
            this._buff = null;
          },
          _flush: function() {
            if (this._buff !== null && this._buff.length !== 0) {
              this._emitValue(this._buff);
              this._buff = [];
            }
          },
          _handleValue: function(x) {
            this._buff.push(x);
            if (this._buff.length >= this._count) {
              this._flush();
            }
          },
          _handleEnd: function() {
            if (this._flushOnEnd) {
              this._flush();
            }
            this._emitEnd();
          }
        };
        var S$29 = createStream("bufferWithCount", mixin$22);
        var P$25 = createProperty("bufferWithCount", mixin$22);
        function bufferWhile$1(obs, count) {
          var _ref2 = arguments.length > 2 && arguments[2] !== undefined ? arguments[2] : {}, _ref2$flushOnEnd = _ref2.flushOnEnd, flushOnEnd = _ref2$flushOnEnd === undefined ? true : _ref2$flushOnEnd;
          return new (obs._ofSameType(S$29, P$25))(obs, { count, flushOnEnd });
        }
        var mixin$23 = {
          _init: function(_ref) {
            var _this = this;
            var { wait, count, flushOnEnd } = _ref;
            this._wait = wait;
            this._count = count;
            this._flushOnEnd = flushOnEnd;
            this._intervalId = null;
            this._$onTick = function() {
              return _this._flush();
            };
            this._buff = [];
          },
          _free: function() {
            this._$onTick = null;
            this._buff = null;
          },
          _flush: function() {
            if (this._buff !== null) {
              this._emitValue(this._buff);
              this._buff = [];
            }
          },
          _handleValue: function(x) {
            this._buff.push(x);
            if (this._buff.length >= this._count) {
              clearInterval(this._intervalId);
              this._flush();
              this._intervalId = setInterval(this._$onTick, this._wait);
            }
          },
          _handleEnd: function() {
            if (this._flushOnEnd && this._buff.length !== 0) {
              this._flush();
            }
            this._emitEnd();
          },
          _onActivation: function() {
            this._intervalId = setInterval(this._$onTick, this._wait);
            this._source.onAny(this._$handleAny);
          },
          _onDeactivation: function() {
            if (this._intervalId !== null) {
              clearInterval(this._intervalId);
              this._intervalId = null;
            }
            this._source.offAny(this._$handleAny);
          }
        };
        var S$30 = createStream("bufferWithTimeOrCount", mixin$23);
        var P$26 = createProperty("bufferWithTimeOrCount", mixin$23);
        function bufferWithTimeOrCount(obs, wait, count) {
          var _ref2 = arguments.length > 3 && arguments[3] !== undefined ? arguments[3] : {}, _ref2$flushOnEnd = _ref2.flushOnEnd, flushOnEnd = _ref2$flushOnEnd === undefined ? true : _ref2$flushOnEnd;
          return new (obs._ofSameType(S$30, P$26))(obs, { wait, count, flushOnEnd });
        }
        function xformForObs(obs) {
          return {
            "@@transducer/step": function(res, input) {
              obs._emitValue(input);
              return null;
            },
            "@@transducer/result": function() {
              obs._emitEnd();
              return null;
            }
          };
        }
        var mixin$24 = {
          _init: function(_ref) {
            var transducer = _ref.transducer;
            this._xform = transducer(xformForObs(this));
          },
          _free: function() {
            this._xform = null;
          },
          _handleValue: function(x) {
            if (this._xform["@@transducer/step"](null, x) !== null) {
              this._xform["@@transducer/result"](null);
            }
          },
          _handleEnd: function() {
            this._xform["@@transducer/result"](null);
          }
        };
        var S$31 = createStream("transduce", mixin$24);
        var P$27 = createProperty("transduce", mixin$24);
        function transduce(obs, transducer) {
          return new (obs._ofSameType(S$31, P$27))(obs, { transducer });
        }
        var mixin$25 = {
          _init: function(_ref) {
            var fn = _ref.fn;
            this._handler = fn;
            this._emitter = emitter(this);
          },
          _free: function() {
            this._handler = null;
            this._emitter = null;
          },
          _handleAny: function(event) {
            this._handler(this._emitter, event);
          }
        };
        var S$32 = createStream("withHandler", mixin$25);
        var P$28 = createProperty("withHandler", mixin$25);
        function withHandler(obs, fn) {
          return new (obs._ofSameType(S$32, P$28))(obs, { fn });
        }
        var isArray = Array.isArray || function(xs) {
          return Object.prototype.toString.call(xs) === "[object Array]";
        };
        function Zip(sources, combinator) {
          var _this = this;
          Stream.call(this);
          this._buffers = map(sources, function(source) {
            return isArray(source) ? cloneArray(source) : [];
          });
          this._sources = map(sources, function(source) {
            return isArray(source) ? never() : source;
          });
          this._combinator = combinator ? spread(combinator, this._sources.length) : function(x) {
            return x;
          };
          this._aliveCount = 0;
          this._$handlers = [];
          var _loop = function(i2) {
            _this._$handlers.push(function(event) {
              return _this._handleAny(i2, event);
            });
          };
          for (var i = 0;i < this._sources.length; i++) {
            _loop(i);
          }
        }
        inherit(Zip, Stream, {
          _name: "zip",
          _onActivation: function() {
            while (this._isFull()) {
              this._emit();
            }
            var length = this._sources.length;
            this._aliveCount = length;
            for (var i = 0;i < length && this._active; i++) {
              this._sources[i].onAny(this._$handlers[i]);
            }
          },
          _onDeactivation: function() {
            for (var i = 0;i < this._sources.length; i++) {
              this._sources[i].offAny(this._$handlers[i]);
            }
          },
          _emit: function() {
            var values = new Array(this._buffers.length);
            for (var i = 0;i < this._buffers.length; i++) {
              values[i] = this._buffers[i].shift();
            }
            var combinator = this._combinator;
            this._emitValue(combinator(values));
          },
          _isFull: function() {
            for (var i = 0;i < this._buffers.length; i++) {
              if (this._buffers[i].length === 0) {
                return false;
              }
            }
            return true;
          },
          _handleAny: function(i, event) {
            if (event.type === VALUE) {
              this._buffers[i].push(event.value);
              if (this._isFull()) {
                this._emit();
              }
            }
            if (event.type === ERROR) {
              this._emitError(event.value);
            }
            if (event.type === END) {
              this._aliveCount--;
              if (this._aliveCount === 0) {
                this._emitEnd();
              }
            }
          },
          _clear: function() {
            Stream.prototype._clear.call(this);
            this._sources = null;
            this._buffers = null;
            this._combinator = null;
            this._$handlers = null;
          }
        });
        function zip(observables, combinator) {
          return observables.length === 0 ? never() : new Zip(observables, combinator);
        }
        var id$8 = function(x) {
          return x;
        };
        function AbstractPool() {
          var _this = this;
          var _ref = arguments.length > 0 && arguments[0] !== undefined ? arguments[0] : {}, _ref$queueLim = _ref.queueLim, queueLim = _ref$queueLim === undefined ? 0 : _ref$queueLim, _ref$concurLim = _ref.concurLim, concurLim = _ref$concurLim === undefined ? -1 : _ref$concurLim, _ref$drop = _ref.drop, drop = _ref$drop === undefined ? "new" : _ref$drop;
          Stream.call(this);
          this._queueLim = queueLim < 0 ? -1 : queueLim;
          this._concurLim = concurLim < 0 ? -1 : concurLim;
          this._drop = drop;
          this._queue = [];
          this._curSources = [];
          this._$handleSubAny = function(event) {
            return _this._handleSubAny(event);
          };
          this._$endHandlers = [];
          this._currentlyAdding = null;
          if (this._concurLim === 0) {
            this._emitEnd();
          }
        }
        inherit(AbstractPool, Stream, {
          _name: "abstractPool",
          _add: function(obj, toObs) {
            toObs = toObs || id$8;
            if (this._concurLim === -1 || this._curSources.length < this._concurLim) {
              this._addToCur(toObs(obj));
            } else {
              if (this._queueLim === -1 || this._queue.length < this._queueLim) {
                this._addToQueue(toObs(obj));
              } else if (this._drop === "old") {
                this._removeOldest();
                this._add(obj, toObs);
              }
            }
          },
          _addAll: function(obss) {
            var _this2 = this;
            forEach(obss, function(obs) {
              return _this2._add(obs);
            });
          },
          _remove: function(obs) {
            if (this._removeCur(obs) === -1) {
              this._removeQueue(obs);
            }
          },
          _addToQueue: function(obs) {
            this._queue = concat(this._queue, [obs]);
          },
          _addToCur: function(obs) {
            if (this._active) {
              if (!obs._alive) {
                if (obs._currentEvent) {
                  this._emit(obs._currentEvent.type, obs._currentEvent.value);
                }
                if (this._active) {
                  if (this._queue.length !== 0) {
                    this._pullQueue();
                  } else if (this._curSources.length === 0) {
                    this._onEmpty();
                  }
                }
                return;
              }
              this._currentlyAdding = obs;
              obs.onAny(this._$handleSubAny);
              this._currentlyAdding = null;
              if (obs._alive) {
                this._curSources = concat(this._curSources, [obs]);
                if (this._active) {
                  this._subToEnd(obs);
                }
              } else {
                if (this._queue.length !== 0) {
                  this._pullQueue();
                } else if (this._curSources.length === 0) {
                  this._onEmpty();
                }
              }
            } else {
              this._curSources = concat(this._curSources, [obs]);
            }
          },
          _subToEnd: function(obs) {
            var _this3 = this;
            var onEnd = function() {
              return _this3._removeCur(obs);
            };
            this._$endHandlers.push({ obs, handler: onEnd });
            obs.onEnd(onEnd);
          },
          _subscribe: function(obs) {
            obs.onAny(this._$handleSubAny);
            if (this._active) {
              this._subToEnd(obs);
            }
          },
          _unsubscribe: function(obs) {
            obs.offAny(this._$handleSubAny);
            var onEndI = findByPred(this._$endHandlers, function(obj) {
              return obj.obs === obs;
            });
            if (onEndI !== -1) {
              obs.offEnd(this._$endHandlers[onEndI].handler);
              this._$endHandlers.splice(onEndI, 1);
            }
          },
          _handleSubAny: function(event) {
            if (event.type === VALUE) {
              this._emitValue(event.value);
            } else if (event.type === ERROR) {
              this._emitError(event.value);
            }
          },
          _removeQueue: function(obs) {
            var index = find(this._queue, obs);
            this._queue = remove(this._queue, index);
            return index;
          },
          _removeCur: function(obs) {
            if (this._active) {
              this._unsubscribe(obs);
            }
            var index = find(this._curSources, obs);
            this._curSources = remove(this._curSources, index);
            if (index !== -1) {
              if (this._queue.length !== 0) {
                this._pullQueue();
              } else if (this._curSources.length === 0) {
                this._onEmpty();
              }
            }
            return index;
          },
          _removeOldest: function() {
            this._removeCur(this._curSources[0]);
          },
          _pullQueue: function() {
            if (this._queue.length !== 0) {
              this._queue = cloneArray(this._queue);
              this._addToCur(this._queue.shift());
            }
          },
          _onActivation: function() {
            for (var i = 0, sources = this._curSources;i < sources.length && this._active; i++) {
              this._subscribe(sources[i]);
            }
          },
          _onDeactivation: function() {
            for (var i = 0, sources = this._curSources;i < sources.length; i++) {
              this._unsubscribe(sources[i]);
            }
            if (this._currentlyAdding !== null) {
              this._unsubscribe(this._currentlyAdding);
            }
          },
          _isEmpty: function() {
            return this._curSources.length === 0;
          },
          _onEmpty: function() {},
          _clear: function() {
            Stream.prototype._clear.call(this);
            this._queue = null;
            this._curSources = null;
            this._$handleSubAny = null;
            this._$endHandlers = null;
          }
        });
        function Merge(sources) {
          AbstractPool.call(this);
          this._addAll(sources);
          this._initialised = true;
        }
        inherit(Merge, AbstractPool, {
          _name: "merge",
          _onEmpty: function() {
            if (this._initialised) {
              this._emitEnd();
            }
          }
        });
        function merge(observables) {
          return observables.length === 0 ? never() : new Merge(observables);
        }
        function S$33(generator) {
          var _this = this;
          Stream.call(this);
          this._generator = generator;
          this._source = null;
          this._inLoop = false;
          this._iteration = 0;
          this._$handleAny = function(event) {
            return _this._handleAny(event);
          };
        }
        inherit(S$33, Stream, {
          _name: "repeat",
          _handleAny: function(event) {
            if (event.type === END) {
              this._source = null;
              this._getSource();
            } else {
              this._emit(event.type, event.value);
            }
          },
          _getSource: function() {
            if (!this._inLoop) {
              this._inLoop = true;
              var generator = this._generator;
              while (this._source === null && this._alive && this._active) {
                this._source = generator(this._iteration++);
                if (this._source) {
                  this._source.onAny(this._$handleAny);
                } else {
                  this._emitEnd();
                }
              }
              this._inLoop = false;
            }
          },
          _onActivation: function() {
            if (this._source) {
              this._source.onAny(this._$handleAny);
            } else {
              this._getSource();
            }
          },
          _onDeactivation: function() {
            if (this._source) {
              this._source.offAny(this._$handleAny);
            }
          },
          _clear: function() {
            Stream.prototype._clear.call(this);
            this._generator = null;
            this._source = null;
            this._$handleAny = null;
          }
        });
        var repeat = function(generator) {
          return new S$33(generator);
        };
        function concat$1(observables) {
          return repeat(function(index) {
            return observables.length > index ? observables[index] : false;
          }).setName("concat");
        }
        function Pool() {
          AbstractPool.call(this);
        }
        inherit(Pool, AbstractPool, {
          _name: "pool",
          plug: function(obs) {
            this._add(obs);
            return this;
          },
          unplug: function(obs) {
            this._remove(obs);
            return this;
          }
        });
        function FlatMap(source, fn, options) {
          var _this = this;
          AbstractPool.call(this, options);
          this._source = source;
          this._fn = fn;
          this._mainEnded = false;
          this._lastCurrent = null;
          this._$handleMain = function(event) {
            return _this._handleMain(event);
          };
        }
        inherit(FlatMap, AbstractPool, {
          _onActivation: function() {
            AbstractPool.prototype._onActivation.call(this);
            if (this._active) {
              this._source.onAny(this._$handleMain);
            }
          },
          _onDeactivation: function() {
            AbstractPool.prototype._onDeactivation.call(this);
            this._source.offAny(this._$handleMain);
            this._hadNoEvSinceDeact = true;
          },
          _handleMain: function(event) {
            if (event.type === VALUE) {
              var sameCurr = this._activating && this._hadNoEvSinceDeact && this._lastCurrent === event.value;
              if (!sameCurr) {
                this._add(event.value, this._fn);
              }
              this._lastCurrent = event.value;
              this._hadNoEvSinceDeact = false;
            }
            if (event.type === ERROR) {
              this._emitError(event.value);
            }
            if (event.type === END) {
              if (this._isEmpty()) {
                this._emitEnd();
              } else {
                this._mainEnded = true;
              }
            }
          },
          _onEmpty: function() {
            if (this._mainEnded) {
              this._emitEnd();
            }
          },
          _clear: function() {
            AbstractPool.prototype._clear.call(this);
            this._source = null;
            this._lastCurrent = null;
            this._$handleMain = null;
          }
        });
        function FlatMapErrors(source, fn) {
          FlatMap.call(this, source, fn);
        }
        inherit(FlatMapErrors, FlatMap, {
          _handleMain: function(event) {
            if (event.type === ERROR) {
              var sameCurr = this._activating && this._hadNoEvSinceDeact && this._lastCurrent === event.value;
              if (!sameCurr) {
                this._add(event.value, this._fn);
              }
              this._lastCurrent = event.value;
              this._hadNoEvSinceDeact = false;
            }
            if (event.type === VALUE) {
              this._emitValue(event.value);
            }
            if (event.type === END) {
              if (this._isEmpty()) {
                this._emitEnd();
              } else {
                this._mainEnded = true;
              }
            }
          }
        });
        function createConstructor$1(BaseClass, name) {
          return function AnonymousObservable(primary, secondary, options) {
            var _this = this;
            BaseClass.call(this);
            this._primary = primary;
            this._secondary = secondary;
            this._name = primary._name + "." + name;
            this._lastSecondary = NOTHING;
            this._$handleSecondaryAny = function(event) {
              return _this._handleSecondaryAny(event);
            };
            this._$handlePrimaryAny = function(event) {
              return _this._handlePrimaryAny(event);
            };
            this._init(options);
          };
        }
        function createClassMethods$1(BaseClass) {
          return {
            _init: function() {},
            _free: function() {},
            _handlePrimaryValue: function(x) {
              this._emitValue(x);
            },
            _handlePrimaryError: function(x) {
              this._emitError(x);
            },
            _handlePrimaryEnd: function() {
              this._emitEnd();
            },
            _handleSecondaryValue: function(x) {
              this._lastSecondary = x;
            },
            _handleSecondaryError: function(x) {
              this._emitError(x);
            },
            _handleSecondaryEnd: function() {},
            _handlePrimaryAny: function(event) {
              switch (event.type) {
                case VALUE:
                  return this._handlePrimaryValue(event.value);
                case ERROR:
                  return this._handlePrimaryError(event.value);
                case END:
                  return this._handlePrimaryEnd(event.value);
              }
            },
            _handleSecondaryAny: function(event) {
              switch (event.type) {
                case VALUE:
                  return this._handleSecondaryValue(event.value);
                case ERROR:
                  return this._handleSecondaryError(event.value);
                case END:
                  this._handleSecondaryEnd(event.value);
                  this._removeSecondary();
              }
            },
            _removeSecondary: function() {
              if (this._secondary !== null) {
                this._secondary.offAny(this._$handleSecondaryAny);
                this._$handleSecondaryAny = null;
                this._secondary = null;
              }
            },
            _onActivation: function() {
              if (this._secondary !== null) {
                this._secondary.onAny(this._$handleSecondaryAny);
              }
              if (this._active) {
                this._primary.onAny(this._$handlePrimaryAny);
              }
            },
            _onDeactivation: function() {
              if (this._secondary !== null) {
                this._secondary.offAny(this._$handleSecondaryAny);
              }
              this._primary.offAny(this._$handlePrimaryAny);
            },
            _clear: function() {
              BaseClass.prototype._clear.call(this);
              this._primary = null;
              this._secondary = null;
              this._lastSecondary = null;
              this._$handleSecondaryAny = null;
              this._$handlePrimaryAny = null;
              this._free();
            }
          };
        }
        function createStream$1(name, mixin2) {
          var S2 = createConstructor$1(Stream, name);
          inherit(S2, Stream, createClassMethods$1(Stream), mixin2);
          return S2;
        }
        function createProperty$1(name, mixin2) {
          var P2 = createConstructor$1(Property, name);
          inherit(P2, Property, createClassMethods$1(Property), mixin2);
          return P2;
        }
        var mixin$26 = {
          _handlePrimaryValue: function(x) {
            if (this._lastSecondary !== NOTHING && this._lastSecondary) {
              this._emitValue(x);
            }
          },
          _handleSecondaryEnd: function() {
            if (this._lastSecondary === NOTHING || !this._lastSecondary) {
              this._emitEnd();
            }
          }
        };
        var S$34 = createStream$1("filterBy", mixin$26);
        var P$29 = createProperty$1("filterBy", mixin$26);
        function filterBy(primary, secondary) {
          return new (primary._ofSameType(S$34, P$29))(primary, secondary);
        }
        var id2 = function(_, x) {
          return x;
        };
        function sampledBy(passive, active, combinator) {
          var _combinator = combinator ? function(a, b) {
            return combinator(b, a);
          } : id2;
          return combine([active], [passive], _combinator).setName(passive, "sampledBy");
        }
        var mixin$27 = {
          _handlePrimaryValue: function(x) {
            if (this._lastSecondary !== NOTHING) {
              this._emitValue(x);
            }
          },
          _handleSecondaryEnd: function() {
            if (this._lastSecondary === NOTHING) {
              this._emitEnd();
            }
          }
        };
        var S$35 = createStream$1("skipUntilBy", mixin$27);
        var P$30 = createProperty$1("skipUntilBy", mixin$27);
        function skipUntilBy(primary, secondary) {
          return new (primary._ofSameType(S$35, P$30))(primary, secondary);
        }
        var mixin$28 = {
          _handleSecondaryValue: function() {
            this._emitEnd();
          }
        };
        var S$36 = createStream$1("takeUntilBy", mixin$28);
        var P$31 = createProperty$1("takeUntilBy", mixin$28);
        function takeUntilBy(primary, secondary) {
          return new (primary._ofSameType(S$36, P$31))(primary, secondary);
        }
        var mixin$29 = {
          _init: function() {
            var _ref = arguments.length > 0 && arguments[0] !== undefined ? arguments[0] : {}, _ref$flushOnEnd = _ref.flushOnEnd, flushOnEnd = _ref$flushOnEnd === undefined ? true : _ref$flushOnEnd;
            this._buff = [];
            this._flushOnEnd = flushOnEnd;
          },
          _free: function() {
            this._buff = null;
          },
          _flush: function() {
            if (this._buff !== null) {
              this._emitValue(this._buff);
              this._buff = [];
            }
          },
          _handlePrimaryEnd: function() {
            if (this._flushOnEnd) {
              this._flush();
            }
            this._emitEnd();
          },
          _onActivation: function() {
            this._primary.onAny(this._$handlePrimaryAny);
            if (this._alive && this._secondary !== null) {
              this._secondary.onAny(this._$handleSecondaryAny);
            }
          },
          _handlePrimaryValue: function(x) {
            this._buff.push(x);
          },
          _handleSecondaryValue: function() {
            this._flush();
          },
          _handleSecondaryEnd: function() {
            if (!this._flushOnEnd) {
              this._emitEnd();
            }
          }
        };
        var S$37 = createStream$1("bufferBy", mixin$29);
        var P$32 = createProperty$1("bufferBy", mixin$29);
        function bufferBy(primary, secondary, options) {
          return new (primary._ofSameType(S$37, P$32))(primary, secondary, options);
        }
        var mixin$30 = {
          _init: function() {
            var _ref = arguments.length > 0 && arguments[0] !== undefined ? arguments[0] : {}, _ref$flushOnEnd = _ref.flushOnEnd, flushOnEnd = _ref$flushOnEnd === undefined ? true : _ref$flushOnEnd, _ref$flushOnChange = _ref.flushOnChange, flushOnChange = _ref$flushOnChange === undefined ? false : _ref$flushOnChange;
            this._buff = [];
            this._flushOnEnd = flushOnEnd;
            this._flushOnChange = flushOnChange;
          },
          _free: function() {
            this._buff = null;
          },
          _flush: function() {
            if (this._buff !== null) {
              this._emitValue(this._buff);
              this._buff = [];
            }
          },
          _handlePrimaryEnd: function() {
            if (this._flushOnEnd) {
              this._flush();
            }
            this._emitEnd();
          },
          _handlePrimaryValue: function(x) {
            this._buff.push(x);
            if (this._lastSecondary !== NOTHING && !this._lastSecondary) {
              this._flush();
            }
          },
          _handleSecondaryEnd: function() {
            if (!this._flushOnEnd && (this._lastSecondary === NOTHING || this._lastSecondary)) {
              this._emitEnd();
            }
          },
          _handleSecondaryValue: function(x) {
            if (this._flushOnChange && !x) {
              this._flush();
            }
            this._lastSecondary = x;
          }
        };
        var S$38 = createStream$1("bufferWhileBy", mixin$30);
        var P$33 = createProperty$1("bufferWhileBy", mixin$30);
        function bufferWhileBy(primary, secondary, options) {
          return new (primary._ofSameType(S$38, P$33))(primary, secondary, options);
        }
        var f = function() {
          return false;
        };
        var t2 = function() {
          return true;
        };
        function awaiting(a, b) {
          var result2 = merge([map$1(a, t2), map$1(b, f)]);
          result2 = skipDuplicates(result2);
          result2 = toProperty(result2, f);
          return result2.setName(a, "awaiting");
        }
        var mixin$31 = {
          _init: function(_ref) {
            var fn = _ref.fn;
            this._fn = fn;
          },
          _free: function() {
            this._fn = null;
          },
          _handleValue: function(x) {
            var fn = this._fn;
            var result2 = fn(x);
            if (result2.convert) {
              this._emitError(result2.error);
            } else {
              this._emitValue(x);
            }
          }
        };
        var S$39 = createStream("valuesToErrors", mixin$31);
        var P$34 = createProperty("valuesToErrors", mixin$31);
        var defFn = function(x) {
          return { convert: true, error: x };
        };
        function valuesToErrors(obs) {
          var fn = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : defFn;
          return new (obs._ofSameType(S$39, P$34))(obs, { fn });
        }
        var mixin$32 = {
          _init: function(_ref) {
            var fn = _ref.fn;
            this._fn = fn;
          },
          _free: function() {
            this._fn = null;
          },
          _handleError: function(x) {
            var fn = this._fn;
            var result2 = fn(x);
            if (result2.convert) {
              this._emitValue(result2.value);
            } else {
              this._emitError(x);
            }
          }
        };
        var S$40 = createStream("errorsToValues", mixin$32);
        var P$35 = createProperty("errorsToValues", mixin$32);
        var defFn$1 = function(x) {
          return { convert: true, value: x };
        };
        function errorsToValues(obs) {
          var fn = arguments.length > 1 && arguments[1] !== undefined ? arguments[1] : defFn$1;
          return new (obs._ofSameType(S$40, P$35))(obs, { fn });
        }
        var mixin$33 = {
          _handleError: function(x) {
            this._emitError(x);
            this._emitEnd();
          }
        };
        var S$41 = createStream("endOnError", mixin$33);
        var P$36 = createProperty("endOnError", mixin$33);
        function endOnError(obs) {
          return new (obs._ofSameType(S$41, P$36))(obs);
        }
        Observable.prototype.toProperty = function(fn) {
          return toProperty(this, fn);
        };
        Observable.prototype.changes = function() {
          return changes(this);
        };
        Observable.prototype.toPromise = function(Promise2) {
          return toPromise(this, Promise2);
        };
        Observable.prototype.toESObservable = toESObservable;
        Observable.prototype[$$observable] = toESObservable;
        Observable.prototype.map = function(fn) {
          return map$1(this, fn);
        };
        Observable.prototype.filter = function(fn) {
          return filter(this, fn);
        };
        Observable.prototype.take = function(n) {
          return take(this, n);
        };
        Observable.prototype.takeErrors = function(n) {
          return takeErrors(this, n);
        };
        Observable.prototype.takeWhile = function(fn) {
          return takeWhile(this, fn);
        };
        Observable.prototype.last = function() {
          return last2(this);
        };
        Observable.prototype.skip = function(n) {
          return skip(this, n);
        };
        Observable.prototype.skipWhile = function(fn) {
          return skipWhile(this, fn);
        };
        Observable.prototype.skipDuplicates = function(fn) {
          return skipDuplicates(this, fn);
        };
        Observable.prototype.diff = function(fn, seed) {
          return diff(this, fn, seed);
        };
        Observable.prototype.scan = function(fn, seed) {
          return scan(this, fn, seed);
        };
        Observable.prototype.flatten = function(fn) {
          return flatten2(this, fn);
        };
        Observable.prototype.delay = function(wait) {
          return delay(this, wait);
        };
        Observable.prototype.throttle = function(wait, options) {
          return throttle(this, wait, options);
        };
        Observable.prototype.debounce = function(wait, options) {
          return debounce(this, wait, options);
        };
        Observable.prototype.mapErrors = function(fn) {
          return mapErrors(this, fn);
        };
        Observable.prototype.filterErrors = function(fn) {
          return filterErrors(this, fn);
        };
        Observable.prototype.ignoreValues = function() {
          return ignoreValues(this);
        };
        Observable.prototype.ignoreErrors = function() {
          return ignoreErrors(this);
        };
        Observable.prototype.ignoreEnd = function() {
          return ignoreEnd(this);
        };
        Observable.prototype.beforeEnd = function(fn) {
          return beforeEnd(this, fn);
        };
        Observable.prototype.slidingWindow = function(max, min) {
          return slidingWindow(this, max, min);
        };
        Observable.prototype.bufferWhile = function(fn, options) {
          return bufferWhile(this, fn, options);
        };
        Observable.prototype.bufferWithCount = function(count, options) {
          return bufferWhile$1(this, count, options);
        };
        Observable.prototype.bufferWithTimeOrCount = function(wait, count, options) {
          return bufferWithTimeOrCount(this, wait, count, options);
        };
        Observable.prototype.transduce = function(transducer) {
          return transduce(this, transducer);
        };
        Observable.prototype.withHandler = function(fn) {
          return withHandler(this, fn);
        };
        Observable.prototype.thru = function(fn) {
          return fn(this);
        };
        Observable.prototype.combine = function(other, combinator) {
          return combine([this, other], combinator);
        };
        Observable.prototype.zip = function(other, combinator) {
          return zip([this, other], combinator);
        };
        Observable.prototype.merge = function(other) {
          return merge([this, other]);
        };
        Observable.prototype.concat = function(other) {
          return concat$1([this, other]);
        };
        var pool = function() {
          return new Pool;
        };
        Observable.prototype.flatMap = function(fn) {
          return new FlatMap(this, fn).setName(this, "flatMap");
        };
        Observable.prototype.flatMapLatest = function(fn) {
          return new FlatMap(this, fn, { concurLim: 1, drop: "old" }).setName(this, "flatMapLatest");
        };
        Observable.prototype.flatMapFirst = function(fn) {
          return new FlatMap(this, fn, { concurLim: 1 }).setName(this, "flatMapFirst");
        };
        Observable.prototype.flatMapConcat = function(fn) {
          return new FlatMap(this, fn, { queueLim: -1, concurLim: 1 }).setName(this, "flatMapConcat");
        };
        Observable.prototype.flatMapConcurLimit = function(fn, limit) {
          return new FlatMap(this, fn, { queueLim: -1, concurLim: limit }).setName(this, "flatMapConcurLimit");
        };
        Observable.prototype.flatMapErrors = function(fn) {
          return new FlatMapErrors(this, fn).setName(this, "flatMapErrors");
        };
        Observable.prototype.filterBy = function(other) {
          return filterBy(this, other);
        };
        Observable.prototype.sampledBy = function(other, combinator) {
          return sampledBy(this, other, combinator);
        };
        Observable.prototype.skipUntilBy = function(other) {
          return skipUntilBy(this, other);
        };
        Observable.prototype.takeUntilBy = function(other) {
          return takeUntilBy(this, other);
        };
        Observable.prototype.bufferBy = function(other, options) {
          return bufferBy(this, other, options);
        };
        Observable.prototype.bufferWhileBy = function(other, options) {
          return bufferWhileBy(this, other, options);
        };
        var DEPRECATION_WARNINGS = true;
        function dissableDeprecationWarnings() {
          DEPRECATION_WARNINGS = false;
        }
        function warn(msg) {
          if (DEPRECATION_WARNINGS && console && typeof console.warn === "function") {
            var msg2 = `
Here is an Error object for you containing the call stack:`;
            console.warn(msg, msg2, new Error);
          }
        }
        Observable.prototype.awaiting = function(other) {
          warn("You are using deprecated .awaiting() method, see https://github.com/kefirjs/kefir/issues/145");
          return awaiting(this, other);
        };
        Observable.prototype.valuesToErrors = function(fn) {
          warn("You are using deprecated .valuesToErrors() method, see https://github.com/kefirjs/kefir/issues/149");
          return valuesToErrors(this, fn);
        };
        Observable.prototype.errorsToValues = function(fn) {
          warn("You are using deprecated .errorsToValues() method, see https://github.com/kefirjs/kefir/issues/149");
          return errorsToValues(this, fn);
        };
        Observable.prototype.endOnError = function() {
          warn("You are using deprecated .endOnError() method, see https://github.com/kefirjs/kefir/issues/150");
          return endOnError(this);
        };
        var Kefir = {
          Observable,
          Stream,
          Property,
          never,
          later,
          interval,
          sequentially,
          fromPoll,
          withInterval,
          fromCallback,
          fromNodeCallback,
          fromEvents,
          stream,
          constant,
          constantError,
          fromPromise,
          fromESObservable,
          combine,
          zip,
          merge,
          concat: concat$1,
          Pool,
          pool,
          repeat,
          staticLand
        };
        Kefir.Kefir = Kefir;
        const __WEBPACK_DEFAULT_EXPORT__ = Kefir;
      },
      7230: (module, __unused_webpack_exports, __webpack_require__2) => {
        var getNative = __webpack_require__2(3984), root = __webpack_require__2(9107);
        var DataView = getNative(root, "DataView");
        module.exports = DataView;
      },
      3435: (module, __unused_webpack_exports, __webpack_require__2) => {
        var hashClear = __webpack_require__2(6890), hashDelete = __webpack_require__2(9484), hashGet = __webpack_require__2(7215), hashHas = __webpack_require__2(7811), hashSet = __webpack_require__2(747);
        function Hash(entries) {
          var index = -1, length = entries == null ? 0 : entries.length;
          this.clear();
          while (++index < length) {
            var entry = entries[index];
            this.set(entry[0], entry[1]);
          }
        }
        Hash.prototype.clear = hashClear;
        Hash.prototype["delete"] = hashDelete;
        Hash.prototype.get = hashGet;
        Hash.prototype.has = hashHas;
        Hash.prototype.set = hashSet;
        module.exports = Hash;
      },
      5217: (module, __unused_webpack_exports, __webpack_require__2) => {
        var listCacheClear = __webpack_require__2(4412), listCacheDelete = __webpack_require__2(8522), listCacheGet = __webpack_require__2(469), listCacheHas = __webpack_require__2(1161), listCacheSet = __webpack_require__2(1441);
        function ListCache(entries) {
          var index = -1, length = entries == null ? 0 : entries.length;
          this.clear();
          while (++index < length) {
            var entry = entries[index];
            this.set(entry[0], entry[1]);
          }
        }
        ListCache.prototype.clear = listCacheClear;
        ListCache.prototype["delete"] = listCacheDelete;
        ListCache.prototype.get = listCacheGet;
        ListCache.prototype.has = listCacheHas;
        ListCache.prototype.set = listCacheSet;
        module.exports = ListCache;
      },
      5661: (module, __unused_webpack_exports, __webpack_require__2) => {
        var getNative = __webpack_require__2(3984), root = __webpack_require__2(9107);
        var Map2 = getNative(root, "Map");
        module.exports = Map2;
      },
      3287: (module, __unused_webpack_exports, __webpack_require__2) => {
        var mapCacheClear = __webpack_require__2(8206), mapCacheDelete = __webpack_require__2(9768), mapCacheGet = __webpack_require__2(6827), mapCacheHas = __webpack_require__2(663), mapCacheSet = __webpack_require__2(5135);
        function MapCache(entries) {
          var index = -1, length = entries == null ? 0 : entries.length;
          this.clear();
          while (++index < length) {
            var entry = entries[index];
            this.set(entry[0], entry[1]);
          }
        }
        MapCache.prototype.clear = mapCacheClear;
        MapCache.prototype["delete"] = mapCacheDelete;
        MapCache.prototype.get = mapCacheGet;
        MapCache.prototype.has = mapCacheHas;
        MapCache.prototype.set = mapCacheSet;
        module.exports = MapCache;
      },
      9102: (module, __unused_webpack_exports, __webpack_require__2) => {
        var getNative = __webpack_require__2(3984), root = __webpack_require__2(9107);
        var Promise2 = getNative(root, "Promise");
        module.exports = Promise2;
      },
      5963: (module, __unused_webpack_exports, __webpack_require__2) => {
        var getNative = __webpack_require__2(3984), root = __webpack_require__2(9107);
        var Set2 = getNative(root, "Set");
        module.exports = Set2;
      },
      1641: (module, __unused_webpack_exports, __webpack_require__2) => {
        var MapCache = __webpack_require__2(3287), setCacheAdd = __webpack_require__2(2486), setCacheHas = __webpack_require__2(9361);
        function SetCache(values) {
          var index = -1, length = values == null ? 0 : values.length;
          this.__data__ = new MapCache;
          while (++index < length) {
            this.add(values[index]);
          }
        }
        SetCache.prototype.add = SetCache.prototype.push = setCacheAdd;
        SetCache.prototype.has = setCacheHas;
        module.exports = SetCache;
      },
      6435: (module, __unused_webpack_exports, __webpack_require__2) => {
        var ListCache = __webpack_require__2(5217), stackClear = __webpack_require__2(8658), stackDelete = __webpack_require__2(3844), stackGet = __webpack_require__2(6503), stackHas = __webpack_require__2(1563), stackSet = __webpack_require__2(259);
        function Stack(entries) {
          var data = this.__data__ = new ListCache(entries);
          this.size = data.size;
        }
        Stack.prototype.clear = stackClear;
        Stack.prototype["delete"] = stackDelete;
        Stack.prototype.get = stackGet;
        Stack.prototype.has = stackHas;
        Stack.prototype.set = stackSet;
        module.exports = Stack;
      },
      6711: (module, __unused_webpack_exports, __webpack_require__2) => {
        var root = __webpack_require__2(9107);
        var Symbol2 = root.Symbol;
        module.exports = Symbol2;
      },
      9282: (module, __unused_webpack_exports, __webpack_require__2) => {
        var root = __webpack_require__2(9107);
        var Uint8Array = root.Uint8Array;
        module.exports = Uint8Array;
      },
      2850: (module, __unused_webpack_exports, __webpack_require__2) => {
        var getNative = __webpack_require__2(3984), root = __webpack_require__2(9107);
        var WeakMap2 = getNative(root, "WeakMap");
        module.exports = WeakMap2;
      },
      807: (module) => {
        function apply(func, thisArg, args) {
          switch (args.length) {
            case 0:
              return func.call(thisArg);
            case 1:
              return func.call(thisArg, args[0]);
            case 2:
              return func.call(thisArg, args[0], args[1]);
            case 3:
              return func.call(thisArg, args[0], args[1], args[2]);
          }
          return func.apply(thisArg, args);
        }
        module.exports = apply;
      },
      3643: (module) => {
        function arrayEach(array, iteratee) {
          var index = -1, length = array == null ? 0 : array.length;
          while (++index < length) {
            if (iteratee(array[index], index, array) === false) {
              break;
            }
          }
          return array;
        }
        module.exports = arrayEach;
      },
      3928: (module) => {
        function arrayFilter(array, predicate) {
          var index = -1, length = array == null ? 0 : array.length, resIndex = 0, result = [];
          while (++index < length) {
            var value = array[index];
            if (predicate(value, index, array)) {
              result[resIndex++] = value;
            }
          }
          return result;
        }
        module.exports = arrayFilter;
      },
      3271: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseIndexOf = __webpack_require__2(8357);
        function arrayIncludes(array, value) {
          var length = array == null ? 0 : array.length;
          return !!length && baseIndexOf(array, value, 0) > -1;
        }
        module.exports = arrayIncludes;
      },
      7599: (module) => {
        function arrayIncludesWith(array, value, comparator) {
          var index = -1, length = array == null ? 0 : array.length;
          while (++index < length) {
            if (comparator(value, array[index])) {
              return true;
            }
          }
          return false;
        }
        module.exports = arrayIncludesWith;
      },
      7137: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseTimes = __webpack_require__2(5410), isArguments = __webpack_require__2(2382), isArray = __webpack_require__2(2003), isBuffer = __webpack_require__2(1262), isIndex = __webpack_require__2(2615), isTypedArray = __webpack_require__2(9221);
        var objectProto = Object.prototype;
        var hasOwnProperty = objectProto.hasOwnProperty;
        function arrayLikeKeys(value, inherited) {
          var isArr = isArray(value), isArg = !isArr && isArguments(value), isBuff = !isArr && !isArg && isBuffer(value), isType = !isArr && !isArg && !isBuff && isTypedArray(value), skipIndexes = isArr || isArg || isBuff || isType, result = skipIndexes ? baseTimes(value.length, String) : [], length = result.length;
          for (var key in value) {
            if ((inherited || hasOwnProperty.call(value, key)) && !(skipIndexes && (key == "length" || isBuff && (key == "offset" || key == "parent") || isType && (key == "buffer" || key == "byteLength" || key == "byteOffset") || isIndex(key, length)))) {
              result.push(key);
            }
          }
          return result;
        }
        module.exports = arrayLikeKeys;
      },
      14: (module) => {
        function arrayMap(array, iteratee) {
          var index = -1, length = array == null ? 0 : array.length, result = Array(length);
          while (++index < length) {
            result[index] = iteratee(array[index], index, array);
          }
          return result;
        }
        module.exports = arrayMap;
      },
      562: (module) => {
        function arrayPush(array, values) {
          var index = -1, length = values.length, offset = array.length;
          while (++index < length) {
            array[offset + index] = values[index];
          }
          return array;
        }
        module.exports = arrayPush;
      },
      9854: (module) => {
        function arraySome(array, predicate) {
          var index = -1, length = array == null ? 0 : array.length;
          while (++index < length) {
            if (predicate(array[index], index, array)) {
              return true;
            }
          }
          return false;
        }
        module.exports = arraySome;
      },
      6645: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseAssignValue = __webpack_require__2(9330), eq = __webpack_require__2(8330);
        var objectProto = Object.prototype;
        var hasOwnProperty = objectProto.hasOwnProperty;
        function assignValue(object, key, value) {
          var objValue = object[key];
          if (!(hasOwnProperty.call(object, key) && eq(objValue, value)) || value === undefined && !(key in object)) {
            baseAssignValue(object, key, value);
          }
        }
        module.exports = assignValue;
      },
      4767: (module, __unused_webpack_exports, __webpack_require__2) => {
        var eq = __webpack_require__2(8330);
        function assocIndexOf(array, key) {
          var length = array.length;
          while (length--) {
            if (eq(array[length][0], key)) {
              return length;
            }
          }
          return -1;
        }
        module.exports = assocIndexOf;
      },
      383: (module, __unused_webpack_exports, __webpack_require__2) => {
        var copyObject = __webpack_require__2(8113), keys = __webpack_require__2(5304);
        function baseAssign(object, source) {
          return object && copyObject(source, keys(source), object);
        }
        module.exports = baseAssign;
      },
      7844: (module, __unused_webpack_exports, __webpack_require__2) => {
        var copyObject = __webpack_require__2(8113), keysIn = __webpack_require__2(7495);
        function baseAssignIn(object, source) {
          return object && copyObject(source, keysIn(source), object);
        }
        module.exports = baseAssignIn;
      },
      9330: (module, __unused_webpack_exports, __webpack_require__2) => {
        var defineProperty = __webpack_require__2(3009);
        function baseAssignValue(object, key, value) {
          if (key == "__proto__" && defineProperty) {
            defineProperty(object, key, {
              configurable: true,
              enumerable: true,
              value,
              writable: true
            });
          } else {
            object[key] = value;
          }
        }
        module.exports = baseAssignValue;
      },
      9631: (module) => {
        function baseClamp(number, lower, upper) {
          if (number === number) {
            if (upper !== undefined) {
              number = number <= upper ? number : upper;
            }
            if (lower !== undefined) {
              number = number >= lower ? number : lower;
            }
          }
          return number;
        }
        module.exports = baseClamp;
      },
      1937: (module, __unused_webpack_exports, __webpack_require__2) => {
        var Stack = __webpack_require__2(6435), arrayEach = __webpack_require__2(3643), assignValue = __webpack_require__2(6645), baseAssign = __webpack_require__2(383), baseAssignIn = __webpack_require__2(7844), cloneBuffer = __webpack_require__2(2932), copyArray = __webpack_require__2(9061), copySymbols = __webpack_require__2(709), copySymbolsIn = __webpack_require__2(8038), getAllKeys = __webpack_require__2(5760), getAllKeysIn = __webpack_require__2(3183), getTag = __webpack_require__2(695), initCloneArray = __webpack_require__2(9303), initCloneByTag = __webpack_require__2(5385), initCloneObject = __webpack_require__2(3991), isArray = __webpack_require__2(2003), isBuffer = __webpack_require__2(1262), isMap = __webpack_require__2(5652), isObject = __webpack_require__2(5603), isSet = __webpack_require__2(9318), keys = __webpack_require__2(5304), keysIn = __webpack_require__2(7495);
        var CLONE_DEEP_FLAG = 1, CLONE_FLAT_FLAG = 2, CLONE_SYMBOLS_FLAG = 4;
        var argsTag = "[object Arguments]", arrayTag = "[object Array]", boolTag = "[object Boolean]", dateTag = "[object Date]", errorTag = "[object Error]", funcTag = "[object Function]", genTag = "[object GeneratorFunction]", mapTag = "[object Map]", numberTag = "[object Number]", objectTag = "[object Object]", regexpTag = "[object RegExp]", setTag = "[object Set]", stringTag = "[object String]", symbolTag = "[object Symbol]", weakMapTag = "[object WeakMap]";
        var arrayBufferTag = "[object ArrayBuffer]", dataViewTag = "[object DataView]", float32Tag = "[object Float32Array]", float64Tag = "[object Float64Array]", int8Tag = "[object Int8Array]", int16Tag = "[object Int16Array]", int32Tag = "[object Int32Array]", uint8Tag = "[object Uint8Array]", uint8ClampedTag = "[object Uint8ClampedArray]", uint16Tag = "[object Uint16Array]", uint32Tag = "[object Uint32Array]";
        var cloneableTags = {};
        cloneableTags[argsTag] = cloneableTags[arrayTag] = cloneableTags[arrayBufferTag] = cloneableTags[dataViewTag] = cloneableTags[boolTag] = cloneableTags[dateTag] = cloneableTags[float32Tag] = cloneableTags[float64Tag] = cloneableTags[int8Tag] = cloneableTags[int16Tag] = cloneableTags[int32Tag] = cloneableTags[mapTag] = cloneableTags[numberTag] = cloneableTags[objectTag] = cloneableTags[regexpTag] = cloneableTags[setTag] = cloneableTags[stringTag] = cloneableTags[symbolTag] = cloneableTags[uint8Tag] = cloneableTags[uint8ClampedTag] = cloneableTags[uint16Tag] = cloneableTags[uint32Tag] = true;
        cloneableTags[errorTag] = cloneableTags[funcTag] = cloneableTags[weakMapTag] = false;
        function baseClone(value, bitmask, customizer, key, object, stack) {
          var result, isDeep = bitmask & CLONE_DEEP_FLAG, isFlat = bitmask & CLONE_FLAT_FLAG, isFull = bitmask & CLONE_SYMBOLS_FLAG;
          if (customizer) {
            result = object ? customizer(value, key, object, stack) : customizer(value);
          }
          if (result !== undefined) {
            return result;
          }
          if (!isObject(value)) {
            return value;
          }
          var isArr = isArray(value);
          if (isArr) {
            result = initCloneArray(value);
            if (!isDeep) {
              return copyArray(value, result);
            }
          } else {
            var tag = getTag(value), isFunc = tag == funcTag || tag == genTag;
            if (isBuffer(value)) {
              return cloneBuffer(value, isDeep);
            }
            if (tag == objectTag || tag == argsTag || isFunc && !object) {
              result = isFlat || isFunc ? {} : initCloneObject(value);
              if (!isDeep) {
                return isFlat ? copySymbolsIn(value, baseAssignIn(result, value)) : copySymbols(value, baseAssign(result, value));
              }
            } else {
              if (!cloneableTags[tag]) {
                return object ? value : {};
              }
              result = initCloneByTag(value, tag, isDeep);
            }
          }
          stack || (stack = new Stack);
          var stacked = stack.get(value);
          if (stacked) {
            return stacked;
          }
          stack.set(value, result);
          if (isSet(value)) {
            value.forEach(function(subValue) {
              result.add(baseClone(subValue, bitmask, customizer, subValue, value, stack));
            });
          } else if (isMap(value)) {
            value.forEach(function(subValue, key2) {
              result.set(key2, baseClone(subValue, bitmask, customizer, key2, value, stack));
            });
          }
          var keysFunc = isFull ? isFlat ? getAllKeysIn : getAllKeys : isFlat ? keysIn : keys;
          var props = isArr ? undefined : keysFunc(value);
          arrayEach(props || value, function(subValue, key2) {
            if (props) {
              key2 = subValue;
              subValue = value[key2];
            }
            assignValue(result, key2, baseClone(subValue, bitmask, customizer, key2, value, stack));
          });
          return result;
        }
        module.exports = baseClone;
      },
      3962: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isObject = __webpack_require__2(5603);
        var objectCreate = Object.create;
        var baseCreate = function() {
          function object() {}
          return function(proto) {
            if (!isObject(proto)) {
              return {};
            }
            if (objectCreate) {
              return objectCreate(proto);
            }
            object.prototype = proto;
            var result = new object;
            object.prototype = undefined;
            return result;
          };
        }();
        module.exports = baseCreate;
      },
      7587: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseForOwn = __webpack_require__2(427), createBaseEach = __webpack_require__2(3679);
        var baseEach = createBaseEach(baseForOwn);
        module.exports = baseEach;
      },
      4384: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseEach = __webpack_require__2(7587);
        function baseFilter(collection, predicate) {
          var result = [];
          baseEach(collection, function(value, index, collection2) {
            if (predicate(value, index, collection2)) {
              result.push(value);
            }
          });
          return result;
        }
        module.exports = baseFilter;
      },
      6917: (module) => {
        function baseFindIndex(array, predicate, fromIndex, fromRight) {
          var length = array.length, index = fromIndex + (fromRight ? 1 : -1);
          while (fromRight ? index-- : ++index < length) {
            if (predicate(array[index], index, array)) {
              return index;
            }
          }
          return -1;
        }
        module.exports = baseFindIndex;
      },
      4958: (module, __unused_webpack_exports, __webpack_require__2) => {
        var arrayPush = __webpack_require__2(562), isFlattenable = __webpack_require__2(4385);
        function baseFlatten(array, depth, predicate, isStrict, result) {
          var index = -1, length = array.length;
          predicate || (predicate = isFlattenable);
          result || (result = []);
          while (++index < length) {
            var value = array[index];
            if (depth > 0 && predicate(value)) {
              if (depth > 1) {
                baseFlatten(value, depth - 1, predicate, isStrict, result);
              } else {
                arrayPush(result, value);
              }
            } else if (!isStrict) {
              result[result.length] = value;
            }
          }
          return result;
        }
        module.exports = baseFlatten;
      },
      1595: (module, __unused_webpack_exports, __webpack_require__2) => {
        var createBaseFor = __webpack_require__2(951);
        var baseFor = createBaseFor();
        module.exports = baseFor;
      },
      427: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseFor = __webpack_require__2(1595), keys = __webpack_require__2(5304);
        function baseForOwn(object, iteratee) {
          return object && baseFor(object, iteratee, keys);
        }
        module.exports = baseForOwn;
      },
      384: (module, __unused_webpack_exports, __webpack_require__2) => {
        var castPath = __webpack_require__2(4275), toKey = __webpack_require__2(8059);
        function baseGet(object, path) {
          path = castPath(path, object);
          var index = 0, length = path.length;
          while (object != null && index < length) {
            object = object[toKey(path[index++])];
          }
          return index && index == length ? object : undefined;
        }
        module.exports = baseGet;
      },
      8821: (module, __unused_webpack_exports, __webpack_require__2) => {
        var arrayPush = __webpack_require__2(562), isArray = __webpack_require__2(2003);
        function baseGetAllKeys(object, keysFunc, symbolsFunc) {
          var result = keysFunc(object);
          return isArray(object) ? result : arrayPush(result, symbolsFunc(object));
        }
        module.exports = baseGetAllKeys;
      },
      6522: (module, __unused_webpack_exports, __webpack_require__2) => {
        var Symbol2 = __webpack_require__2(6711), getRawTag = __webpack_require__2(905), objectToString = __webpack_require__2(2588);
        var nullTag = "[object Null]", undefinedTag = "[object Undefined]";
        var symToStringTag = Symbol2 ? Symbol2.toStringTag : undefined;
        function baseGetTag(value) {
          if (value == null) {
            return value === undefined ? undefinedTag : nullTag;
          }
          return symToStringTag && symToStringTag in Object(value) ? getRawTag(value) : objectToString(value);
        }
        module.exports = baseGetTag;
      },
      8772: (module) => {
        var objectProto = Object.prototype;
        var hasOwnProperty = objectProto.hasOwnProperty;
        function baseHas(object, key) {
          return object != null && hasOwnProperty.call(object, key);
        }
        module.exports = baseHas;
      },
      6571: (module) => {
        function baseHasIn(object, key) {
          return object != null && key in Object(object);
        }
        module.exports = baseHasIn;
      },
      8357: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseFindIndex = __webpack_require__2(6917), baseIsNaN = __webpack_require__2(3001), strictIndexOf = __webpack_require__2(5957);
        function baseIndexOf(array, value, fromIndex) {
          return value === value ? strictIndexOf(array, value, fromIndex) : baseFindIndex(array, baseIsNaN, fromIndex);
        }
        module.exports = baseIndexOf;
      },
      739: (module, __unused_webpack_exports, __webpack_require__2) => {
        var SetCache = __webpack_require__2(1641), arrayIncludes = __webpack_require__2(3271), arrayIncludesWith = __webpack_require__2(7599), arrayMap = __webpack_require__2(14), baseUnary = __webpack_require__2(2347), cacheHas = __webpack_require__2(7585);
        var nativeMin = Math.min;
        function baseIntersection(arrays, iteratee, comparator) {
          var includes = comparator ? arrayIncludesWith : arrayIncludes, length = arrays[0].length, othLength = arrays.length, othIndex = othLength, caches = Array(othLength), maxLength = Infinity, result = [];
          while (othIndex--) {
            var array = arrays[othIndex];
            if (othIndex && iteratee) {
              array = arrayMap(array, baseUnary(iteratee));
            }
            maxLength = nativeMin(array.length, maxLength);
            caches[othIndex] = !comparator && (iteratee || length >= 120 && array.length >= 120) ? new SetCache(othIndex && array) : undefined;
          }
          array = arrays[0];
          var index = -1, seen = caches[0];
          outer:
            while (++index < length && result.length < maxLength) {
              var value = array[index], computed = iteratee ? iteratee(value) : value;
              value = comparator || value !== 0 ? value : 0;
              if (!(seen ? cacheHas(seen, computed) : includes(result, computed, comparator))) {
                othIndex = othLength;
                while (--othIndex) {
                  var cache = caches[othIndex];
                  if (!(cache ? cacheHas(cache, computed) : includes(arrays[othIndex], computed, comparator))) {
                    continue outer;
                  }
                }
                if (seen) {
                  seen.push(computed);
                }
                result.push(value);
              }
            }
          return result;
        }
        module.exports = baseIntersection;
      },
      2744: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseGetTag = __webpack_require__2(6522), isObjectLike = __webpack_require__2(2620);
        var argsTag = "[object Arguments]";
        function baseIsArguments(value) {
          return isObjectLike(value) && baseGetTag(value) == argsTag;
        }
        module.exports = baseIsArguments;
      },
      9336: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseIsEqualDeep = __webpack_require__2(1894), isObjectLike = __webpack_require__2(2620);
        function baseIsEqual(value, other, bitmask, customizer, stack) {
          if (value === other) {
            return true;
          }
          if (value == null || other == null || !isObjectLike(value) && !isObjectLike(other)) {
            return value !== value && other !== other;
          }
          return baseIsEqualDeep(value, other, bitmask, customizer, baseIsEqual, stack);
        }
        module.exports = baseIsEqual;
      },
      1894: (module, __unused_webpack_exports, __webpack_require__2) => {
        var Stack = __webpack_require__2(6435), equalArrays = __webpack_require__2(1505), equalByTag = __webpack_require__2(9620), equalObjects = __webpack_require__2(439), getTag = __webpack_require__2(695), isArray = __webpack_require__2(2003), isBuffer = __webpack_require__2(1262), isTypedArray = __webpack_require__2(9221);
        var COMPARE_PARTIAL_FLAG = 1;
        var argsTag = "[object Arguments]", arrayTag = "[object Array]", objectTag = "[object Object]";
        var objectProto = Object.prototype;
        var hasOwnProperty = objectProto.hasOwnProperty;
        function baseIsEqualDeep(object, other, bitmask, customizer, equalFunc, stack) {
          var objIsArr = isArray(object), othIsArr = isArray(other), objTag = objIsArr ? arrayTag : getTag(object), othTag = othIsArr ? arrayTag : getTag(other);
          objTag = objTag == argsTag ? objectTag : objTag;
          othTag = othTag == argsTag ? objectTag : othTag;
          var objIsObj = objTag == objectTag, othIsObj = othTag == objectTag, isSameTag = objTag == othTag;
          if (isSameTag && isBuffer(object)) {
            if (!isBuffer(other)) {
              return false;
            }
            objIsArr = true;
            objIsObj = false;
          }
          if (isSameTag && !objIsObj) {
            stack || (stack = new Stack);
            return objIsArr || isTypedArray(object) ? equalArrays(object, other, bitmask, customizer, equalFunc, stack) : equalByTag(object, other, objTag, bitmask, customizer, equalFunc, stack);
          }
          if (!(bitmask & COMPARE_PARTIAL_FLAG)) {
            var objIsWrapped = objIsObj && hasOwnProperty.call(object, "__wrapped__"), othIsWrapped = othIsObj && hasOwnProperty.call(other, "__wrapped__");
            if (objIsWrapped || othIsWrapped) {
              var objUnwrapped = objIsWrapped ? object.value() : object, othUnwrapped = othIsWrapped ? other.value() : other;
              stack || (stack = new Stack);
              return equalFunc(objUnwrapped, othUnwrapped, bitmask, customizer, stack);
            }
          }
          if (!isSameTag) {
            return false;
          }
          stack || (stack = new Stack);
          return equalObjects(object, other, bitmask, customizer, equalFunc, stack);
        }
        module.exports = baseIsEqualDeep;
      },
      8742: (module, __unused_webpack_exports, __webpack_require__2) => {
        var getTag = __webpack_require__2(695), isObjectLike = __webpack_require__2(2620);
        var mapTag = "[object Map]";
        function baseIsMap(value) {
          return isObjectLike(value) && getTag(value) == mapTag;
        }
        module.exports = baseIsMap;
      },
      4253: (module, __unused_webpack_exports, __webpack_require__2) => {
        var Stack = __webpack_require__2(6435), baseIsEqual = __webpack_require__2(9336);
        var COMPARE_PARTIAL_FLAG = 1, COMPARE_UNORDERED_FLAG = 2;
        function baseIsMatch(object, source, matchData, customizer) {
          var index = matchData.length, length = index, noCustomizer = !customizer;
          if (object == null) {
            return !length;
          }
          object = Object(object);
          while (index--) {
            var data = matchData[index];
            if (noCustomizer && data[2] ? data[1] !== object[data[0]] : !(data[0] in object)) {
              return false;
            }
          }
          while (++index < length) {
            data = matchData[index];
            var key = data[0], objValue = object[key], srcValue = data[1];
            if (noCustomizer && data[2]) {
              if (objValue === undefined && !(key in object)) {
                return false;
              }
            } else {
              var stack = new Stack;
              if (customizer) {
                var result = customizer(objValue, srcValue, key, object, source, stack);
              }
              if (!(result === undefined ? baseIsEqual(srcValue, objValue, COMPARE_PARTIAL_FLAG | COMPARE_UNORDERED_FLAG, customizer, stack) : result)) {
                return false;
              }
            }
          }
          return true;
        }
        module.exports = baseIsMatch;
      },
      3001: (module) => {
        function baseIsNaN(value) {
          return value !== value;
        }
        module.exports = baseIsNaN;
      },
      2249: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isFunction = __webpack_require__2(8148), isMasked = __webpack_require__2(1398), isObject = __webpack_require__2(5603), toSource = __webpack_require__2(1543);
        var reRegExpChar = /[\\^$.*+?()[\]{}|]/g;
        var reIsHostCtor = /^\[object .+?Constructor\]$/;
        var funcProto = Function.prototype, objectProto = Object.prototype;
        var funcToString = funcProto.toString;
        var hasOwnProperty = objectProto.hasOwnProperty;
        var reIsNative = RegExp("^" + funcToString.call(hasOwnProperty).replace(reRegExpChar, "\\$&").replace(/hasOwnProperty|(function).*?(?=\\\()| for .+?(?=\\\])/g, "$1.*?") + "$");
        function baseIsNative(value) {
          if (!isObject(value) || isMasked(value)) {
            return false;
          }
          var pattern = isFunction(value) ? reIsNative : reIsHostCtor;
          return pattern.test(toSource(value));
        }
        module.exports = baseIsNative;
      },
      5476: (module, __unused_webpack_exports, __webpack_require__2) => {
        var getTag = __webpack_require__2(695), isObjectLike = __webpack_require__2(2620);
        var setTag = "[object Set]";
        function baseIsSet(value) {
          return isObjectLike(value) && getTag(value) == setTag;
        }
        module.exports = baseIsSet;
      },
      5387: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseGetTag = __webpack_require__2(6522), isLength = __webpack_require__2(7164), isObjectLike = __webpack_require__2(2620);
        var argsTag = "[object Arguments]", arrayTag = "[object Array]", boolTag = "[object Boolean]", dateTag = "[object Date]", errorTag = "[object Error]", funcTag = "[object Function]", mapTag = "[object Map]", numberTag = "[object Number]", objectTag = "[object Object]", regexpTag = "[object RegExp]", setTag = "[object Set]", stringTag = "[object String]", weakMapTag = "[object WeakMap]";
        var arrayBufferTag = "[object ArrayBuffer]", dataViewTag = "[object DataView]", float32Tag = "[object Float32Array]", float64Tag = "[object Float64Array]", int8Tag = "[object Int8Array]", int16Tag = "[object Int16Array]", int32Tag = "[object Int32Array]", uint8Tag = "[object Uint8Array]", uint8ClampedTag = "[object Uint8ClampedArray]", uint16Tag = "[object Uint16Array]", uint32Tag = "[object Uint32Array]";
        var typedArrayTags = {};
        typedArrayTags[float32Tag] = typedArrayTags[float64Tag] = typedArrayTags[int8Tag] = typedArrayTags[int16Tag] = typedArrayTags[int32Tag] = typedArrayTags[uint8Tag] = typedArrayTags[uint8ClampedTag] = typedArrayTags[uint16Tag] = typedArrayTags[uint32Tag] = true;
        typedArrayTags[argsTag] = typedArrayTags[arrayTag] = typedArrayTags[arrayBufferTag] = typedArrayTags[boolTag] = typedArrayTags[dataViewTag] = typedArrayTags[dateTag] = typedArrayTags[errorTag] = typedArrayTags[funcTag] = typedArrayTags[mapTag] = typedArrayTags[numberTag] = typedArrayTags[objectTag] = typedArrayTags[regexpTag] = typedArrayTags[setTag] = typedArrayTags[stringTag] = typedArrayTags[weakMapTag] = false;
        function baseIsTypedArray(value) {
          return isObjectLike(value) && isLength(value.length) && !!typedArrayTags[baseGetTag(value)];
        }
        module.exports = baseIsTypedArray;
      },
      7675: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseMatches = __webpack_require__2(5141), baseMatchesProperty = __webpack_require__2(8476), identity = __webpack_require__2(1686), isArray = __webpack_require__2(2003), property = __webpack_require__2(7093);
        function baseIteratee(value) {
          if (typeof value == "function") {
            return value;
          }
          if (value == null) {
            return identity;
          }
          if (typeof value == "object") {
            return isArray(value) ? baseMatchesProperty(value[0], value[1]) : baseMatches(value);
          }
          return property(value);
        }
        module.exports = baseIteratee;
      },
      6794: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isPrototype = __webpack_require__2(6165), nativeKeys = __webpack_require__2(6132);
        var objectProto = Object.prototype;
        var hasOwnProperty = objectProto.hasOwnProperty;
        function baseKeys(object) {
          if (!isPrototype(object)) {
            return nativeKeys(object);
          }
          var result = [];
          for (var key in Object(object)) {
            if (hasOwnProperty.call(object, key) && key != "constructor") {
              result.push(key);
            }
          }
          return result;
        }
        module.exports = baseKeys;
      },
      8157: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isObject = __webpack_require__2(5603), isPrototype = __webpack_require__2(6165), nativeKeysIn = __webpack_require__2(4555);
        var objectProto = Object.prototype;
        var hasOwnProperty = objectProto.hasOwnProperty;
        function baseKeysIn(object) {
          if (!isObject(object)) {
            return nativeKeysIn(object);
          }
          var isProto = isPrototype(object), result = [];
          for (var key in object) {
            if (!(key == "constructor" && (isProto || !hasOwnProperty.call(object, key)))) {
              result.push(key);
            }
          }
          return result;
        }
        module.exports = baseKeysIn;
      },
      5718: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseEach = __webpack_require__2(7587), isArrayLike = __webpack_require__2(6316);
        function baseMap(collection, iteratee) {
          var index = -1, result = isArrayLike(collection) ? Array(collection.length) : [];
          baseEach(collection, function(value, key, collection2) {
            result[++index] = iteratee(value, key, collection2);
          });
          return result;
        }
        module.exports = baseMap;
      },
      5141: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseIsMatch = __webpack_require__2(4253), getMatchData = __webpack_require__2(8418), matchesStrictComparable = __webpack_require__2(3591);
        function baseMatches(source) {
          var matchData = getMatchData(source);
          if (matchData.length == 1 && matchData[0][2]) {
            return matchesStrictComparable(matchData[0][0], matchData[0][1]);
          }
          return function(object) {
            return object === source || baseIsMatch(object, source, matchData);
          };
        }
        module.exports = baseMatches;
      },
      8476: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseIsEqual = __webpack_require__2(9336), get = __webpack_require__2(1214), hasIn = __webpack_require__2(8765), isKey = __webpack_require__2(5456), isStrictComparable = __webpack_require__2(7030), matchesStrictComparable = __webpack_require__2(3591), toKey = __webpack_require__2(8059);
        var COMPARE_PARTIAL_FLAG = 1, COMPARE_UNORDERED_FLAG = 2;
        function baseMatchesProperty(path, srcValue) {
          if (isKey(path) && isStrictComparable(srcValue)) {
            return matchesStrictComparable(toKey(path), srcValue);
          }
          return function(object) {
            var objValue = get(object, path);
            return objValue === undefined && objValue === srcValue ? hasIn(object, path) : baseIsEqual(srcValue, objValue, COMPARE_PARTIAL_FLAG | COMPARE_UNORDERED_FLAG);
          };
        }
        module.exports = baseMatchesProperty;
      },
      3729: (module, __unused_webpack_exports, __webpack_require__2) => {
        var arrayMap = __webpack_require__2(14), baseGet = __webpack_require__2(384), baseIteratee = __webpack_require__2(7675), baseMap = __webpack_require__2(5718), baseSortBy = __webpack_require__2(1163), baseUnary = __webpack_require__2(2347), compareMultiple = __webpack_require__2(7644), identity = __webpack_require__2(1686), isArray = __webpack_require__2(2003);
        function baseOrderBy(collection, iteratees, orders) {
          if (iteratees.length) {
            iteratees = arrayMap(iteratees, function(iteratee) {
              if (isArray(iteratee)) {
                return function(value) {
                  return baseGet(value, iteratee.length === 1 ? iteratee[0] : iteratee);
                };
              }
              return iteratee;
            });
          } else {
            iteratees = [identity];
          }
          var index = -1;
          iteratees = arrayMap(iteratees, baseUnary(baseIteratee));
          var result = baseMap(collection, function(value, key, collection2) {
            var criteria = arrayMap(iteratees, function(iteratee) {
              return iteratee(value);
            });
            return { criteria, index: ++index, value };
          });
          return baseSortBy(result, function(object, other) {
            return compareMultiple(object, other, orders);
          });
        }
        module.exports = baseOrderBy;
      },
      1171: (module) => {
        function baseProperty(key) {
          return function(object) {
            return object == null ? undefined : object[key];
          };
        }
        module.exports = baseProperty;
      },
      4589: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseGet = __webpack_require__2(384);
        function basePropertyDeep(path) {
          return function(object) {
            return baseGet(object, path);
          };
        }
        module.exports = basePropertyDeep;
      },
      9390: (module) => {
        function basePropertyOf(object) {
          return function(key) {
            return object == null ? undefined : object[key];
          };
        }
        module.exports = basePropertyOf;
      },
      3408: (module, __unused_webpack_exports, __webpack_require__2) => {
        var identity = __webpack_require__2(1686), overRest = __webpack_require__2(5683), setToString = __webpack_require__2(6391);
        function baseRest(func, start) {
          return setToString(overRest(func, start, identity), func + "");
        }
        module.exports = baseRest;
      },
      7880: (module, __unused_webpack_exports, __webpack_require__2) => {
        var constant = __webpack_require__2(7660), defineProperty = __webpack_require__2(3009), identity = __webpack_require__2(1686);
        var baseSetToString = !defineProperty ? identity : function(func, string) {
          return defineProperty(func, "toString", {
            configurable: true,
            enumerable: false,
            value: constant(string),
            writable: true
          });
        };
        module.exports = baseSetToString;
      },
      1163: (module) => {
        function baseSortBy(array, comparer) {
          var length = array.length;
          array.sort(comparer);
          while (length--) {
            array[length] = array[length].value;
          }
          return array;
        }
        module.exports = baseSortBy;
      },
      5410: (module) => {
        function baseTimes(n, iteratee) {
          var index = -1, result = Array(n);
          while (++index < n) {
            result[index] = iteratee(index);
          }
          return result;
        }
        module.exports = baseTimes;
      },
      8354: (module, __unused_webpack_exports, __webpack_require__2) => {
        var Symbol2 = __webpack_require__2(6711), arrayMap = __webpack_require__2(14), isArray = __webpack_require__2(2003), isSymbol = __webpack_require__2(6596);
        var INFINITY = 1 / 0;
        var symbolProto = Symbol2 ? Symbol2.prototype : undefined, symbolToString = symbolProto ? symbolProto.toString : undefined;
        function baseToString(value) {
          if (typeof value == "string") {
            return value;
          }
          if (isArray(value)) {
            return arrayMap(value, baseToString) + "";
          }
          if (isSymbol(value)) {
            return symbolToString ? symbolToString.call(value) : "";
          }
          var result = value + "";
          return result == "0" && 1 / value == -INFINITY ? "-0" : result;
        }
        module.exports = baseToString;
      },
      9070: (module, __unused_webpack_exports, __webpack_require__2) => {
        var trimmedEndIndex = __webpack_require__2(8882);
        var reTrimStart = /^\s+/;
        function baseTrim(string) {
          return string ? string.slice(0, trimmedEndIndex(string) + 1).replace(reTrimStart, "") : string;
        }
        module.exports = baseTrim;
      },
      2347: (module) => {
        function baseUnary(func) {
          return function(value) {
            return func(value);
          };
        }
        module.exports = baseUnary;
      },
      7971: (module, __unused_webpack_exports, __webpack_require__2) => {
        var SetCache = __webpack_require__2(1641), arrayIncludes = __webpack_require__2(3271), arrayIncludesWith = __webpack_require__2(7599), cacheHas = __webpack_require__2(7585), createSet = __webpack_require__2(7455), setToArray = __webpack_require__2(5841);
        var LARGE_ARRAY_SIZE = 200;
        function baseUniq(array, iteratee, comparator) {
          var index = -1, includes = arrayIncludes, length = array.length, isCommon = true, result = [], seen = result;
          if (comparator) {
            isCommon = false;
            includes = arrayIncludesWith;
          } else if (length >= LARGE_ARRAY_SIZE) {
            var set = iteratee ? null : createSet(array);
            if (set) {
              return setToArray(set);
            }
            isCommon = false;
            includes = cacheHas;
            seen = new SetCache;
          } else {
            seen = iteratee ? [] : result;
          }
          outer:
            while (++index < length) {
              var value = array[index], computed = iteratee ? iteratee(value) : value;
              value = comparator || value !== 0 ? value : 0;
              if (isCommon && computed === computed) {
                var seenIndex = seen.length;
                while (seenIndex--) {
                  if (seen[seenIndex] === computed) {
                    continue outer;
                  }
                }
                if (iteratee) {
                  seen.push(computed);
                }
                result.push(value);
              } else if (!includes(seen, computed, comparator)) {
                if (seen !== result) {
                  seen.push(computed);
                }
                result.push(value);
              }
            }
          return result;
        }
        module.exports = baseUniq;
      },
      4956: (module, __unused_webpack_exports, __webpack_require__2) => {
        var arrayMap = __webpack_require__2(14);
        function baseValues(object, props) {
          return arrayMap(props, function(key) {
            return object[key];
          });
        }
        module.exports = baseValues;
      },
      7585: (module) => {
        function cacheHas(cache, key) {
          return cache.has(key);
        }
        module.exports = cacheHas;
      },
      9471: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isArrayLikeObject = __webpack_require__2(1899);
        function castArrayLikeObject(value) {
          return isArrayLikeObject(value) ? value : [];
        }
        module.exports = castArrayLikeObject;
      },
      2072: (module, __unused_webpack_exports, __webpack_require__2) => {
        var identity = __webpack_require__2(1686);
        function castFunction(value) {
          return typeof value == "function" ? value : identity;
        }
        module.exports = castFunction;
      },
      4275: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isArray = __webpack_require__2(2003), isKey = __webpack_require__2(5456), stringToPath = __webpack_require__2(5240), toString = __webpack_require__2(7060);
        function castPath(value, object) {
          if (isArray(value)) {
            return value;
          }
          return isKey(value, object) ? [value] : stringToPath(toString(value));
        }
        module.exports = castPath;
      },
      1987: (module, __unused_webpack_exports, __webpack_require__2) => {
        var Uint8Array = __webpack_require__2(9282);
        function cloneArrayBuffer(arrayBuffer) {
          var result = new arrayBuffer.constructor(arrayBuffer.byteLength);
          new Uint8Array(result).set(new Uint8Array(arrayBuffer));
          return result;
        }
        module.exports = cloneArrayBuffer;
      },
      2932: (module, exports, __webpack_require__2) => {
        module = __webpack_require__2.nmd(module);
        var root = __webpack_require__2(9107);
        var freeExports = exports && !exports.nodeType && exports;
        var freeModule = freeExports && true && module && !module.nodeType && module;
        var moduleExports = freeModule && freeModule.exports === freeExports;
        var Buffer = moduleExports ? root.Buffer : undefined, allocUnsafe = Buffer ? Buffer.allocUnsafe : undefined;
        function cloneBuffer(buffer, isDeep) {
          if (isDeep) {
            return buffer.slice();
          }
          var length = buffer.length, result = allocUnsafe ? allocUnsafe(length) : new buffer.constructor(length);
          buffer.copy(result);
          return result;
        }
        module.exports = cloneBuffer;
      },
      3931: (module, __unused_webpack_exports, __webpack_require__2) => {
        var cloneArrayBuffer = __webpack_require__2(1987);
        function cloneDataView(dataView, isDeep) {
          var buffer = isDeep ? cloneArrayBuffer(dataView.buffer) : dataView.buffer;
          return new dataView.constructor(buffer, dataView.byteOffset, dataView.byteLength);
        }
        module.exports = cloneDataView;
      },
      1259: (module) => {
        var reFlags = /\w*$/;
        function cloneRegExp(regexp) {
          var result = new regexp.constructor(regexp.source, reFlags.exec(regexp));
          result.lastIndex = regexp.lastIndex;
          return result;
        }
        module.exports = cloneRegExp;
      },
      6878: (module, __unused_webpack_exports, __webpack_require__2) => {
        var Symbol2 = __webpack_require__2(6711);
        var symbolProto = Symbol2 ? Symbol2.prototype : undefined, symbolValueOf = symbolProto ? symbolProto.valueOf : undefined;
        function cloneSymbol(symbol) {
          return symbolValueOf ? Object(symbolValueOf.call(symbol)) : {};
        }
        module.exports = cloneSymbol;
      },
      3859: (module, __unused_webpack_exports, __webpack_require__2) => {
        var cloneArrayBuffer = __webpack_require__2(1987);
        function cloneTypedArray(typedArray, isDeep) {
          var buffer = isDeep ? cloneArrayBuffer(typedArray.buffer) : typedArray.buffer;
          return new typedArray.constructor(buffer, typedArray.byteOffset, typedArray.length);
        }
        module.exports = cloneTypedArray;
      },
      8452: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isSymbol = __webpack_require__2(6596);
        function compareAscending(value, other) {
          if (value !== other) {
            var valIsDefined = value !== undefined, valIsNull = value === null, valIsReflexive = value === value, valIsSymbol = isSymbol(value);
            var othIsDefined = other !== undefined, othIsNull = other === null, othIsReflexive = other === other, othIsSymbol = isSymbol(other);
            if (!othIsNull && !othIsSymbol && !valIsSymbol && value > other || valIsSymbol && othIsDefined && othIsReflexive && !othIsNull && !othIsSymbol || valIsNull && othIsDefined && othIsReflexive || !valIsDefined && othIsReflexive || !valIsReflexive) {
              return 1;
            }
            if (!valIsNull && !valIsSymbol && !othIsSymbol && value < other || othIsSymbol && valIsDefined && valIsReflexive && !valIsNull && !valIsSymbol || othIsNull && valIsDefined && valIsReflexive || !othIsDefined && valIsReflexive || !othIsReflexive) {
              return -1;
            }
          }
          return 0;
        }
        module.exports = compareAscending;
      },
      7644: (module, __unused_webpack_exports, __webpack_require__2) => {
        var compareAscending = __webpack_require__2(8452);
        function compareMultiple(object, other, orders) {
          var index = -1, objCriteria = object.criteria, othCriteria = other.criteria, length = objCriteria.length, ordersLength = orders.length;
          while (++index < length) {
            var result = compareAscending(objCriteria[index], othCriteria[index]);
            if (result) {
              if (index >= ordersLength) {
                return result;
              }
              var order = orders[index];
              return result * (order == "desc" ? -1 : 1);
            }
          }
          return object.index - other.index;
        }
        module.exports = compareMultiple;
      },
      9061: (module) => {
        function copyArray(source, array) {
          var index = -1, length = source.length;
          array || (array = Array(length));
          while (++index < length) {
            array[index] = source[index];
          }
          return array;
        }
        module.exports = copyArray;
      },
      8113: (module, __unused_webpack_exports, __webpack_require__2) => {
        var assignValue = __webpack_require__2(6645), baseAssignValue = __webpack_require__2(9330);
        function copyObject(source, props, object, customizer) {
          var isNew = !object;
          object || (object = {});
          var index = -1, length = props.length;
          while (++index < length) {
            var key = props[index];
            var newValue = customizer ? customizer(object[key], source[key], key, object, source) : undefined;
            if (newValue === undefined) {
              newValue = source[key];
            }
            if (isNew) {
              baseAssignValue(object, key, newValue);
            } else {
              assignValue(object, key, newValue);
            }
          }
          return object;
        }
        module.exports = copyObject;
      },
      709: (module, __unused_webpack_exports, __webpack_require__2) => {
        var copyObject = __webpack_require__2(8113), getSymbols = __webpack_require__2(6806);
        function copySymbols(source, object) {
          return copyObject(source, getSymbols(source), object);
        }
        module.exports = copySymbols;
      },
      8038: (module, __unused_webpack_exports, __webpack_require__2) => {
        var copyObject = __webpack_require__2(8113), getSymbolsIn = __webpack_require__2(6337);
        function copySymbolsIn(source, object) {
          return copyObject(source, getSymbolsIn(source), object);
        }
        module.exports = copySymbolsIn;
      },
      3887: (module, __unused_webpack_exports, __webpack_require__2) => {
        var root = __webpack_require__2(9107);
        var coreJsData = root["__core-js_shared__"];
        module.exports = coreJsData;
      },
      3679: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isArrayLike = __webpack_require__2(6316);
        function createBaseEach(eachFunc, fromRight) {
          return function(collection, iteratee) {
            if (collection == null) {
              return collection;
            }
            if (!isArrayLike(collection)) {
              return eachFunc(collection, iteratee);
            }
            var length = collection.length, index = fromRight ? length : -1, iterable = Object(collection);
            while (fromRight ? index-- : ++index < length) {
              if (iteratee(iterable[index], index, iterable) === false) {
                break;
              }
            }
            return collection;
          };
        }
        module.exports = createBaseEach;
      },
      951: (module) => {
        function createBaseFor(fromRight) {
          return function(object, iteratee, keysFunc) {
            var index = -1, iterable = Object(object), props = keysFunc(object), length = props.length;
            while (length--) {
              var key = props[fromRight ? length : ++index];
              if (iteratee(iterable[key], key, iterable) === false) {
                break;
              }
            }
            return object;
          };
        }
        module.exports = createBaseFor;
      },
      7216: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseIteratee = __webpack_require__2(7675), isArrayLike = __webpack_require__2(6316), keys = __webpack_require__2(5304);
        function createFind(findIndexFunc) {
          return function(collection, predicate, fromIndex) {
            var iterable = Object(collection);
            if (!isArrayLike(collection)) {
              var iteratee = baseIteratee(predicate, 3);
              collection = keys(collection);
              predicate = function(key) {
                return iteratee(iterable[key], key, iterable);
              };
            }
            var index = findIndexFunc(collection, predicate, fromIndex);
            return index > -1 ? iterable[iteratee ? collection[index] : index] : undefined;
          };
        }
        module.exports = createFind;
      },
      7455: (module, __unused_webpack_exports, __webpack_require__2) => {
        var Set2 = __webpack_require__2(5963), noop = __webpack_require__2(1700), setToArray = __webpack_require__2(5841);
        var INFINITY = 1 / 0;
        var createSet = !(Set2 && 1 / setToArray(new Set2([, -0]))[1] == INFINITY) ? noop : function(values) {
          return new Set2(values);
        };
        module.exports = createSet;
      },
      3009: (module, __unused_webpack_exports, __webpack_require__2) => {
        var getNative = __webpack_require__2(3984);
        var defineProperty = function() {
          try {
            var func = getNative(Object, "defineProperty");
            func({}, "", {});
            return func;
          } catch (e) {}
        }();
        module.exports = defineProperty;
      },
      1505: (module, __unused_webpack_exports, __webpack_require__2) => {
        var SetCache = __webpack_require__2(1641), arraySome = __webpack_require__2(9854), cacheHas = __webpack_require__2(7585);
        var COMPARE_PARTIAL_FLAG = 1, COMPARE_UNORDERED_FLAG = 2;
        function equalArrays(array, other, bitmask, customizer, equalFunc, stack) {
          var isPartial = bitmask & COMPARE_PARTIAL_FLAG, arrLength = array.length, othLength = other.length;
          if (arrLength != othLength && !(isPartial && othLength > arrLength)) {
            return false;
          }
          var arrStacked = stack.get(array);
          var othStacked = stack.get(other);
          if (arrStacked && othStacked) {
            return arrStacked == other && othStacked == array;
          }
          var index = -1, result = true, seen = bitmask & COMPARE_UNORDERED_FLAG ? new SetCache : undefined;
          stack.set(array, other);
          stack.set(other, array);
          while (++index < arrLength) {
            var arrValue = array[index], othValue = other[index];
            if (customizer) {
              var compared = isPartial ? customizer(othValue, arrValue, index, other, array, stack) : customizer(arrValue, othValue, index, array, other, stack);
            }
            if (compared !== undefined) {
              if (compared) {
                continue;
              }
              result = false;
              break;
            }
            if (seen) {
              if (!arraySome(other, function(othValue2, othIndex) {
                if (!cacheHas(seen, othIndex) && (arrValue === othValue2 || equalFunc(arrValue, othValue2, bitmask, customizer, stack))) {
                  return seen.push(othIndex);
                }
              })) {
                result = false;
                break;
              }
            } else if (!(arrValue === othValue || equalFunc(arrValue, othValue, bitmask, customizer, stack))) {
              result = false;
              break;
            }
          }
          stack["delete"](array);
          stack["delete"](other);
          return result;
        }
        module.exports = equalArrays;
      },
      9620: (module, __unused_webpack_exports, __webpack_require__2) => {
        var Symbol2 = __webpack_require__2(6711), Uint8Array = __webpack_require__2(9282), eq = __webpack_require__2(8330), equalArrays = __webpack_require__2(1505), mapToArray = __webpack_require__2(5483), setToArray = __webpack_require__2(5841);
        var COMPARE_PARTIAL_FLAG = 1, COMPARE_UNORDERED_FLAG = 2;
        var boolTag = "[object Boolean]", dateTag = "[object Date]", errorTag = "[object Error]", mapTag = "[object Map]", numberTag = "[object Number]", regexpTag = "[object RegExp]", setTag = "[object Set]", stringTag = "[object String]", symbolTag = "[object Symbol]";
        var arrayBufferTag = "[object ArrayBuffer]", dataViewTag = "[object DataView]";
        var symbolProto = Symbol2 ? Symbol2.prototype : undefined, symbolValueOf = symbolProto ? symbolProto.valueOf : undefined;
        function equalByTag(object, other, tag, bitmask, customizer, equalFunc, stack) {
          switch (tag) {
            case dataViewTag:
              if (object.byteLength != other.byteLength || object.byteOffset != other.byteOffset) {
                return false;
              }
              object = object.buffer;
              other = other.buffer;
            case arrayBufferTag:
              if (object.byteLength != other.byteLength || !equalFunc(new Uint8Array(object), new Uint8Array(other))) {
                return false;
              }
              return true;
            case boolTag:
            case dateTag:
            case numberTag:
              return eq(+object, +other);
            case errorTag:
              return object.name == other.name && object.message == other.message;
            case regexpTag:
            case stringTag:
              return object == other + "";
            case mapTag:
              var convert = mapToArray;
            case setTag:
              var isPartial = bitmask & COMPARE_PARTIAL_FLAG;
              convert || (convert = setToArray);
              if (object.size != other.size && !isPartial) {
                return false;
              }
              var stacked = stack.get(object);
              if (stacked) {
                return stacked == other;
              }
              bitmask |= COMPARE_UNORDERED_FLAG;
              stack.set(object, other);
              var result = equalArrays(convert(object), convert(other), bitmask, customizer, equalFunc, stack);
              stack["delete"](object);
              return result;
            case symbolTag:
              if (symbolValueOf) {
                return symbolValueOf.call(object) == symbolValueOf.call(other);
              }
          }
          return false;
        }
        module.exports = equalByTag;
      },
      439: (module, __unused_webpack_exports, __webpack_require__2) => {
        var getAllKeys = __webpack_require__2(5760);
        var COMPARE_PARTIAL_FLAG = 1;
        var objectProto = Object.prototype;
        var hasOwnProperty = objectProto.hasOwnProperty;
        function equalObjects(object, other, bitmask, customizer, equalFunc, stack) {
          var isPartial = bitmask & COMPARE_PARTIAL_FLAG, objProps = getAllKeys(object), objLength = objProps.length, othProps = getAllKeys(other), othLength = othProps.length;
          if (objLength != othLength && !isPartial) {
            return false;
          }
          var index = objLength;
          while (index--) {
            var key = objProps[index];
            if (!(isPartial ? key in other : hasOwnProperty.call(other, key))) {
              return false;
            }
          }
          var objStacked = stack.get(object);
          var othStacked = stack.get(other);
          if (objStacked && othStacked) {
            return objStacked == other && othStacked == object;
          }
          var result = true;
          stack.set(object, other);
          stack.set(other, object);
          var skipCtor = isPartial;
          while (++index < objLength) {
            key = objProps[index];
            var objValue = object[key], othValue = other[key];
            if (customizer) {
              var compared = isPartial ? customizer(othValue, objValue, key, other, object, stack) : customizer(objValue, othValue, key, object, other, stack);
            }
            if (!(compared === undefined ? objValue === othValue || equalFunc(objValue, othValue, bitmask, customizer, stack) : compared)) {
              result = false;
              break;
            }
            skipCtor || (skipCtor = key == "constructor");
          }
          if (result && !skipCtor) {
            var objCtor = object.constructor, othCtor = other.constructor;
            if (objCtor != othCtor && (("constructor" in object) && ("constructor" in other)) && !(typeof objCtor == "function" && objCtor instanceof objCtor && typeof othCtor == "function" && othCtor instanceof othCtor)) {
              result = false;
            }
          }
          stack["delete"](object);
          stack["delete"](other);
          return result;
        }
        module.exports = equalObjects;
      },
      9025: (module, __unused_webpack_exports, __webpack_require__2) => {
        var basePropertyOf = __webpack_require__2(9390);
        var htmlEscapes = {
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;"
        };
        var escapeHtmlChar = basePropertyOf(htmlEscapes);
        module.exports = escapeHtmlChar;
      },
      2718: (module, __unused_webpack_exports, __webpack_require__2) => {
        var freeGlobal = typeof __webpack_require__2.g == "object" && __webpack_require__2.g && __webpack_require__2.g.Object === Object && __webpack_require__2.g;
        module.exports = freeGlobal;
      },
      5760: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseGetAllKeys = __webpack_require__2(8821), getSymbols = __webpack_require__2(6806), keys = __webpack_require__2(5304);
        function getAllKeys(object) {
          return baseGetAllKeys(object, keys, getSymbols);
        }
        module.exports = getAllKeys;
      },
      3183: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseGetAllKeys = __webpack_require__2(8821), getSymbolsIn = __webpack_require__2(6337), keysIn = __webpack_require__2(7495);
        function getAllKeysIn(object) {
          return baseGetAllKeys(object, keysIn, getSymbolsIn);
        }
        module.exports = getAllKeysIn;
      },
      6929: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isKeyable = __webpack_require__2(9732);
        function getMapData(map, key) {
          var data = map.__data__;
          return isKeyable(key) ? data[typeof key == "string" ? "string" : "hash"] : data.map;
        }
        module.exports = getMapData;
      },
      8418: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isStrictComparable = __webpack_require__2(7030), keys = __webpack_require__2(5304);
        function getMatchData(object) {
          var result = keys(object), length = result.length;
          while (length--) {
            var key = result[length], value = object[key];
            result[length] = [key, value, isStrictComparable(value)];
          }
          return result;
        }
        module.exports = getMatchData;
      },
      3984: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseIsNative = __webpack_require__2(2249), getValue = __webpack_require__2(1074);
        function getNative(object, key) {
          var value = getValue(object, key);
          return baseIsNative(value) ? value : undefined;
        }
        module.exports = getNative;
      },
      5425: (module, __unused_webpack_exports, __webpack_require__2) => {
        var overArg = __webpack_require__2(889);
        var getPrototype = overArg(Object.getPrototypeOf, Object);
        module.exports = getPrototype;
      },
      905: (module, __unused_webpack_exports, __webpack_require__2) => {
        var Symbol2 = __webpack_require__2(6711);
        var objectProto = Object.prototype;
        var hasOwnProperty = objectProto.hasOwnProperty;
        var nativeObjectToString = objectProto.toString;
        var symToStringTag = Symbol2 ? Symbol2.toStringTag : undefined;
        function getRawTag(value) {
          var isOwn = hasOwnProperty.call(value, symToStringTag), tag = value[symToStringTag];
          try {
            value[symToStringTag] = undefined;
            var unmasked = true;
          } catch (e) {}
          var result = nativeObjectToString.call(value);
          if (unmasked) {
            if (isOwn) {
              value[symToStringTag] = tag;
            } else {
              delete value[symToStringTag];
            }
          }
          return result;
        }
        module.exports = getRawTag;
      },
      6806: (module, __unused_webpack_exports, __webpack_require__2) => {
        var arrayFilter = __webpack_require__2(3928), stubArray = __webpack_require__2(119);
        var objectProto = Object.prototype;
        var propertyIsEnumerable = objectProto.propertyIsEnumerable;
        var nativeGetSymbols = Object.getOwnPropertySymbols;
        var getSymbols = !nativeGetSymbols ? stubArray : function(object) {
          if (object == null) {
            return [];
          }
          object = Object(object);
          return arrayFilter(nativeGetSymbols(object), function(symbol) {
            return propertyIsEnumerable.call(object, symbol);
          });
        };
        module.exports = getSymbols;
      },
      6337: (module, __unused_webpack_exports, __webpack_require__2) => {
        var arrayPush = __webpack_require__2(562), getPrototype = __webpack_require__2(5425), getSymbols = __webpack_require__2(6806), stubArray = __webpack_require__2(119);
        var nativeGetSymbols = Object.getOwnPropertySymbols;
        var getSymbolsIn = !nativeGetSymbols ? stubArray : function(object) {
          var result = [];
          while (object) {
            arrayPush(result, getSymbols(object));
            object = getPrototype(object);
          }
          return result;
        };
        module.exports = getSymbolsIn;
      },
      695: (module, __unused_webpack_exports, __webpack_require__2) => {
        var DataView = __webpack_require__2(7230), Map2 = __webpack_require__2(5661), Promise2 = __webpack_require__2(9102), Set2 = __webpack_require__2(5963), WeakMap2 = __webpack_require__2(2850), baseGetTag = __webpack_require__2(6522), toSource = __webpack_require__2(1543);
        var mapTag = "[object Map]", objectTag = "[object Object]", promiseTag = "[object Promise]", setTag = "[object Set]", weakMapTag = "[object WeakMap]";
        var dataViewTag = "[object DataView]";
        var dataViewCtorString = toSource(DataView), mapCtorString = toSource(Map2), promiseCtorString = toSource(Promise2), setCtorString = toSource(Set2), weakMapCtorString = toSource(WeakMap2);
        var getTag = baseGetTag;
        if (DataView && getTag(new DataView(new ArrayBuffer(1))) != dataViewTag || Map2 && getTag(new Map2) != mapTag || Promise2 && getTag(Promise2.resolve()) != promiseTag || Set2 && getTag(new Set2) != setTag || WeakMap2 && getTag(new WeakMap2) != weakMapTag) {
          getTag = function(value) {
            var result = baseGetTag(value), Ctor = result == objectTag ? value.constructor : undefined, ctorString = Ctor ? toSource(Ctor) : "";
            if (ctorString) {
              switch (ctorString) {
                case dataViewCtorString:
                  return dataViewTag;
                case mapCtorString:
                  return mapTag;
                case promiseCtorString:
                  return promiseTag;
                case setCtorString:
                  return setTag;
                case weakMapCtorString:
                  return weakMapTag;
              }
            }
            return result;
          };
        }
        module.exports = getTag;
      },
      1074: (module) => {
        function getValue(object, key) {
          return object == null ? undefined : object[key];
        }
        module.exports = getValue;
      },
      2248: (module, __unused_webpack_exports, __webpack_require__2) => {
        var castPath = __webpack_require__2(4275), isArguments = __webpack_require__2(2382), isArray = __webpack_require__2(2003), isIndex = __webpack_require__2(2615), isLength = __webpack_require__2(7164), toKey = __webpack_require__2(8059);
        function hasPath(object, path, hasFunc) {
          path = castPath(path, object);
          var index = -1, length = path.length, result = false;
          while (++index < length) {
            var key = toKey(path[index]);
            if (!(result = object != null && hasFunc(object, key))) {
              break;
            }
            object = object[key];
          }
          if (result || ++index != length) {
            return result;
          }
          length = object == null ? 0 : object.length;
          return !!length && isLength(length) && isIndex(key, length) && (isArray(object) || isArguments(object));
        }
        module.exports = hasPath;
      },
      6890: (module, __unused_webpack_exports, __webpack_require__2) => {
        var nativeCreate = __webpack_require__2(6060);
        function hashClear() {
          this.__data__ = nativeCreate ? nativeCreate(null) : {};
          this.size = 0;
        }
        module.exports = hashClear;
      },
      9484: (module) => {
        function hashDelete(key) {
          var result = this.has(key) && delete this.__data__[key];
          this.size -= result ? 1 : 0;
          return result;
        }
        module.exports = hashDelete;
      },
      7215: (module, __unused_webpack_exports, __webpack_require__2) => {
        var nativeCreate = __webpack_require__2(6060);
        var HASH_UNDEFINED = "__lodash_hash_undefined__";
        var objectProto = Object.prototype;
        var hasOwnProperty = objectProto.hasOwnProperty;
        function hashGet(key) {
          var data = this.__data__;
          if (nativeCreate) {
            var result = data[key];
            return result === HASH_UNDEFINED ? undefined : result;
          }
          return hasOwnProperty.call(data, key) ? data[key] : undefined;
        }
        module.exports = hashGet;
      },
      7811: (module, __unused_webpack_exports, __webpack_require__2) => {
        var nativeCreate = __webpack_require__2(6060);
        var objectProto = Object.prototype;
        var hasOwnProperty = objectProto.hasOwnProperty;
        function hashHas(key) {
          var data = this.__data__;
          return nativeCreate ? data[key] !== undefined : hasOwnProperty.call(data, key);
        }
        module.exports = hashHas;
      },
      747: (module, __unused_webpack_exports, __webpack_require__2) => {
        var nativeCreate = __webpack_require__2(6060);
        var HASH_UNDEFINED = "__lodash_hash_undefined__";
        function hashSet(key, value) {
          var data = this.__data__;
          this.size += this.has(key) ? 0 : 1;
          data[key] = nativeCreate && value === undefined ? HASH_UNDEFINED : value;
          return this;
        }
        module.exports = hashSet;
      },
      9303: (module) => {
        var objectProto = Object.prototype;
        var hasOwnProperty = objectProto.hasOwnProperty;
        function initCloneArray(array) {
          var length = array.length, result = new array.constructor(length);
          if (length && typeof array[0] == "string" && hasOwnProperty.call(array, "index")) {
            result.index = array.index;
            result.input = array.input;
          }
          return result;
        }
        module.exports = initCloneArray;
      },
      5385: (module, __unused_webpack_exports, __webpack_require__2) => {
        var cloneArrayBuffer = __webpack_require__2(1987), cloneDataView = __webpack_require__2(3931), cloneRegExp = __webpack_require__2(1259), cloneSymbol = __webpack_require__2(6878), cloneTypedArray = __webpack_require__2(3859);
        var boolTag = "[object Boolean]", dateTag = "[object Date]", mapTag = "[object Map]", numberTag = "[object Number]", regexpTag = "[object RegExp]", setTag = "[object Set]", stringTag = "[object String]", symbolTag = "[object Symbol]";
        var arrayBufferTag = "[object ArrayBuffer]", dataViewTag = "[object DataView]", float32Tag = "[object Float32Array]", float64Tag = "[object Float64Array]", int8Tag = "[object Int8Array]", int16Tag = "[object Int16Array]", int32Tag = "[object Int32Array]", uint8Tag = "[object Uint8Array]", uint8ClampedTag = "[object Uint8ClampedArray]", uint16Tag = "[object Uint16Array]", uint32Tag = "[object Uint32Array]";
        function initCloneByTag(object, tag, isDeep) {
          var Ctor = object.constructor;
          switch (tag) {
            case arrayBufferTag:
              return cloneArrayBuffer(object);
            case boolTag:
            case dateTag:
              return new Ctor(+object);
            case dataViewTag:
              return cloneDataView(object, isDeep);
            case float32Tag:
            case float64Tag:
            case int8Tag:
            case int16Tag:
            case int32Tag:
            case uint8Tag:
            case uint8ClampedTag:
            case uint16Tag:
            case uint32Tag:
              return cloneTypedArray(object, isDeep);
            case mapTag:
              return new Ctor;
            case numberTag:
            case stringTag:
              return new Ctor(object);
            case regexpTag:
              return cloneRegExp(object);
            case setTag:
              return new Ctor;
            case symbolTag:
              return cloneSymbol(object);
          }
        }
        module.exports = initCloneByTag;
      },
      3991: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseCreate = __webpack_require__2(3962), getPrototype = __webpack_require__2(5425), isPrototype = __webpack_require__2(6165);
        function initCloneObject(object) {
          return typeof object.constructor == "function" && !isPrototype(object) ? baseCreate(getPrototype(object)) : {};
        }
        module.exports = initCloneObject;
      },
      4385: (module, __unused_webpack_exports, __webpack_require__2) => {
        var Symbol2 = __webpack_require__2(6711), isArguments = __webpack_require__2(2382), isArray = __webpack_require__2(2003);
        var spreadableSymbol = Symbol2 ? Symbol2.isConcatSpreadable : undefined;
        function isFlattenable(value) {
          return isArray(value) || isArguments(value) || !!(spreadableSymbol && value && value[spreadableSymbol]);
        }
        module.exports = isFlattenable;
      },
      2615: (module) => {
        var MAX_SAFE_INTEGER = 9007199254740991;
        var reIsUint = /^(?:0|[1-9]\d*)$/;
        function isIndex(value, length) {
          var type = typeof value;
          length = length == null ? MAX_SAFE_INTEGER : length;
          return !!length && (type == "number" || type != "symbol" && reIsUint.test(value)) && (value > -1 && value % 1 == 0 && value < length);
        }
        module.exports = isIndex;
      },
      5934: (module, __unused_webpack_exports, __webpack_require__2) => {
        var eq = __webpack_require__2(8330), isArrayLike = __webpack_require__2(6316), isIndex = __webpack_require__2(2615), isObject = __webpack_require__2(5603);
        function isIterateeCall(value, index, object) {
          if (!isObject(object)) {
            return false;
          }
          var type = typeof index;
          if (type == "number" ? isArrayLike(object) && isIndex(index, object.length) : type == "string" && (index in object)) {
            return eq(object[index], value);
          }
          return false;
        }
        module.exports = isIterateeCall;
      },
      5456: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isArray = __webpack_require__2(2003), isSymbol = __webpack_require__2(6596);
        var reIsDeepProp = /\.|\[(?:[^[\]]*|(["'])(?:(?!\1)[^\\]|\\.)*?\1)\]/, reIsPlainProp = /^\w*$/;
        function isKey(value, object) {
          if (isArray(value)) {
            return false;
          }
          var type = typeof value;
          if (type == "number" || type == "symbol" || type == "boolean" || value == null || isSymbol(value)) {
            return true;
          }
          return reIsPlainProp.test(value) || !reIsDeepProp.test(value) || object != null && value in Object(object);
        }
        module.exports = isKey;
      },
      9732: (module) => {
        function isKeyable(value) {
          var type = typeof value;
          return type == "string" || type == "number" || type == "symbol" || type == "boolean" ? value !== "__proto__" : value === null;
        }
        module.exports = isKeyable;
      },
      1398: (module, __unused_webpack_exports, __webpack_require__2) => {
        var coreJsData = __webpack_require__2(3887);
        var maskSrcKey = function() {
          var uid = /[^.]+$/.exec(coreJsData && coreJsData.keys && coreJsData.keys.IE_PROTO || "");
          return uid ? "Symbol(src)_1." + uid : "";
        }();
        function isMasked(func) {
          return !!maskSrcKey && maskSrcKey in func;
        }
        module.exports = isMasked;
      },
      6165: (module) => {
        var objectProto = Object.prototype;
        function isPrototype(value) {
          var Ctor = value && value.constructor, proto = typeof Ctor == "function" && Ctor.prototype || objectProto;
          return value === proto;
        }
        module.exports = isPrototype;
      },
      7030: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isObject = __webpack_require__2(5603);
        function isStrictComparable(value) {
          return value === value && !isObject(value);
        }
        module.exports = isStrictComparable;
      },
      4412: (module) => {
        function listCacheClear() {
          this.__data__ = [];
          this.size = 0;
        }
        module.exports = listCacheClear;
      },
      8522: (module, __unused_webpack_exports, __webpack_require__2) => {
        var assocIndexOf = __webpack_require__2(4767);
        var arrayProto = Array.prototype;
        var splice = arrayProto.splice;
        function listCacheDelete(key) {
          var data = this.__data__, index = assocIndexOf(data, key);
          if (index < 0) {
            return false;
          }
          var lastIndex = data.length - 1;
          if (index == lastIndex) {
            data.pop();
          } else {
            splice.call(data, index, 1);
          }
          --this.size;
          return true;
        }
        module.exports = listCacheDelete;
      },
      469: (module, __unused_webpack_exports, __webpack_require__2) => {
        var assocIndexOf = __webpack_require__2(4767);
        function listCacheGet(key) {
          var data = this.__data__, index = assocIndexOf(data, key);
          return index < 0 ? undefined : data[index][1];
        }
        module.exports = listCacheGet;
      },
      1161: (module, __unused_webpack_exports, __webpack_require__2) => {
        var assocIndexOf = __webpack_require__2(4767);
        function listCacheHas(key) {
          return assocIndexOf(this.__data__, key) > -1;
        }
        module.exports = listCacheHas;
      },
      1441: (module, __unused_webpack_exports, __webpack_require__2) => {
        var assocIndexOf = __webpack_require__2(4767);
        function listCacheSet(key, value) {
          var data = this.__data__, index = assocIndexOf(data, key);
          if (index < 0) {
            ++this.size;
            data.push([key, value]);
          } else {
            data[index][1] = value;
          }
          return this;
        }
        module.exports = listCacheSet;
      },
      8206: (module, __unused_webpack_exports, __webpack_require__2) => {
        var Hash = __webpack_require__2(3435), ListCache = __webpack_require__2(5217), Map2 = __webpack_require__2(5661);
        function mapCacheClear() {
          this.size = 0;
          this.__data__ = {
            hash: new Hash,
            map: new (Map2 || ListCache),
            string: new Hash
          };
        }
        module.exports = mapCacheClear;
      },
      9768: (module, __unused_webpack_exports, __webpack_require__2) => {
        var getMapData = __webpack_require__2(6929);
        function mapCacheDelete(key) {
          var result = getMapData(this, key)["delete"](key);
          this.size -= result ? 1 : 0;
          return result;
        }
        module.exports = mapCacheDelete;
      },
      6827: (module, __unused_webpack_exports, __webpack_require__2) => {
        var getMapData = __webpack_require__2(6929);
        function mapCacheGet(key) {
          return getMapData(this, key).get(key);
        }
        module.exports = mapCacheGet;
      },
      663: (module, __unused_webpack_exports, __webpack_require__2) => {
        var getMapData = __webpack_require__2(6929);
        function mapCacheHas(key) {
          return getMapData(this, key).has(key);
        }
        module.exports = mapCacheHas;
      },
      5135: (module, __unused_webpack_exports, __webpack_require__2) => {
        var getMapData = __webpack_require__2(6929);
        function mapCacheSet(key, value) {
          var data = getMapData(this, key), size = data.size;
          data.set(key, value);
          this.size += data.size == size ? 0 : 1;
          return this;
        }
        module.exports = mapCacheSet;
      },
      5483: (module) => {
        function mapToArray(map) {
          var index = -1, result = Array(map.size);
          map.forEach(function(value, key) {
            result[++index] = [key, value];
          });
          return result;
        }
        module.exports = mapToArray;
      },
      3591: (module) => {
        function matchesStrictComparable(key, srcValue) {
          return function(object) {
            if (object == null) {
              return false;
            }
            return object[key] === srcValue && (srcValue !== undefined || (key in Object(object)));
          };
        }
        module.exports = matchesStrictComparable;
      },
      874: (module, __unused_webpack_exports, __webpack_require__2) => {
        var memoize = __webpack_require__2(9513);
        var MAX_MEMOIZE_SIZE = 500;
        function memoizeCapped(func) {
          var result = memoize(func, function(key) {
            if (cache.size === MAX_MEMOIZE_SIZE) {
              cache.clear();
            }
            return key;
          });
          var cache = result.cache;
          return result;
        }
        module.exports = memoizeCapped;
      },
      6060: (module, __unused_webpack_exports, __webpack_require__2) => {
        var getNative = __webpack_require__2(3984);
        var nativeCreate = getNative(Object, "create");
        module.exports = nativeCreate;
      },
      6132: (module, __unused_webpack_exports, __webpack_require__2) => {
        var overArg = __webpack_require__2(889);
        var nativeKeys = overArg(Object.keys, Object);
        module.exports = nativeKeys;
      },
      4555: (module) => {
        function nativeKeysIn(object) {
          var result = [];
          if (object != null) {
            for (var key in Object(object)) {
              result.push(key);
            }
          }
          return result;
        }
        module.exports = nativeKeysIn;
      },
      8315: (module, exports, __webpack_require__2) => {
        module = __webpack_require__2.nmd(module);
        var freeGlobal = __webpack_require__2(2718);
        var freeExports = exports && !exports.nodeType && exports;
        var freeModule = freeExports && true && module && !module.nodeType && module;
        var moduleExports = freeModule && freeModule.exports === freeExports;
        var freeProcess = moduleExports && freeGlobal.process;
        var nodeUtil = function() {
          try {
            var types = freeModule && freeModule.require && freeModule.require("util").types;
            if (types) {
              return types;
            }
            return freeProcess && freeProcess.binding && freeProcess.binding("util");
          } catch (e) {}
        }();
        module.exports = nodeUtil;
      },
      2588: (module) => {
        var objectProto = Object.prototype;
        var nativeObjectToString = objectProto.toString;
        function objectToString(value) {
          return nativeObjectToString.call(value);
        }
        module.exports = objectToString;
      },
      889: (module) => {
        function overArg(func, transform) {
          return function(arg) {
            return func(transform(arg));
          };
        }
        module.exports = overArg;
      },
      5683: (module, __unused_webpack_exports, __webpack_require__2) => {
        var apply = __webpack_require__2(807);
        var nativeMax = Math.max;
        function overRest(func, start, transform) {
          start = nativeMax(start === undefined ? func.length - 1 : start, 0);
          return function() {
            var args = arguments, index = -1, length = nativeMax(args.length - start, 0), array = Array(length);
            while (++index < length) {
              array[index] = args[start + index];
            }
            index = -1;
            var otherArgs = Array(start + 1);
            while (++index < start) {
              otherArgs[index] = args[index];
            }
            otherArgs[start] = transform(array);
            return apply(func, this, otherArgs);
          };
        }
        module.exports = overRest;
      },
      9107: (module, __unused_webpack_exports, __webpack_require__2) => {
        var freeGlobal = __webpack_require__2(2718);
        var freeSelf = typeof self == "object" && self && self.Object === Object && self;
        var root = freeGlobal || freeSelf || Function("return this")();
        module.exports = root;
      },
      2486: (module) => {
        var HASH_UNDEFINED = "__lodash_hash_undefined__";
        function setCacheAdd(value) {
          this.__data__.set(value, HASH_UNDEFINED);
          return this;
        }
        module.exports = setCacheAdd;
      },
      9361: (module) => {
        function setCacheHas(value) {
          return this.__data__.has(value);
        }
        module.exports = setCacheHas;
      },
      5841: (module) => {
        function setToArray(set) {
          var index = -1, result = Array(set.size);
          set.forEach(function(value) {
            result[++index] = value;
          });
          return result;
        }
        module.exports = setToArray;
      },
      6391: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseSetToString = __webpack_require__2(7880), shortOut = __webpack_require__2(9437);
        var setToString = shortOut(baseSetToString);
        module.exports = setToString;
      },
      9437: (module) => {
        var HOT_COUNT = 800, HOT_SPAN = 16;
        var nativeNow = Date.now;
        function shortOut(func) {
          var count = 0, lastCalled = 0;
          return function() {
            var stamp = nativeNow(), remaining = HOT_SPAN - (stamp - lastCalled);
            lastCalled = stamp;
            if (remaining > 0) {
              if (++count >= HOT_COUNT) {
                return arguments[0];
              }
            } else {
              count = 0;
            }
            return func.apply(undefined, arguments);
          };
        }
        module.exports = shortOut;
      },
      8658: (module, __unused_webpack_exports, __webpack_require__2) => {
        var ListCache = __webpack_require__2(5217);
        function stackClear() {
          this.__data__ = new ListCache;
          this.size = 0;
        }
        module.exports = stackClear;
      },
      3844: (module) => {
        function stackDelete(key) {
          var data = this.__data__, result = data["delete"](key);
          this.size = data.size;
          return result;
        }
        module.exports = stackDelete;
      },
      6503: (module) => {
        function stackGet(key) {
          return this.__data__.get(key);
        }
        module.exports = stackGet;
      },
      1563: (module) => {
        function stackHas(key) {
          return this.__data__.has(key);
        }
        module.exports = stackHas;
      },
      259: (module, __unused_webpack_exports, __webpack_require__2) => {
        var ListCache = __webpack_require__2(5217), Map2 = __webpack_require__2(5661), MapCache = __webpack_require__2(3287);
        var LARGE_ARRAY_SIZE = 200;
        function stackSet(key, value) {
          var data = this.__data__;
          if (data instanceof ListCache) {
            var pairs = data.__data__;
            if (!Map2 || pairs.length < LARGE_ARRAY_SIZE - 1) {
              pairs.push([key, value]);
              this.size = ++data.size;
              return this;
            }
            data = this.__data__ = new MapCache(pairs);
          }
          data.set(key, value);
          this.size = data.size;
          return this;
        }
        module.exports = stackSet;
      },
      5957: (module) => {
        function strictIndexOf(array, value, fromIndex) {
          var index = fromIndex - 1, length = array.length;
          while (++index < length) {
            if (array[index] === value) {
              return index;
            }
          }
          return -1;
        }
        module.exports = strictIndexOf;
      },
      5240: (module, __unused_webpack_exports, __webpack_require__2) => {
        var memoizeCapped = __webpack_require__2(874);
        var rePropName = /[^.[\]]+|\[(?:(-?\d+(?:\.\d+)?)|(["'])((?:(?!\2)[^\\]|\\.)*?)\2)\]|(?=(?:\.|\[\])(?:\.|\[\]|$))/g;
        var reEscapeChar = /\\(\\)?/g;
        var stringToPath = memoizeCapped(function(string) {
          var result = [];
          if (string.charCodeAt(0) === 46) {
            result.push("");
          }
          string.replace(rePropName, function(match, number, quote, subString) {
            result.push(quote ? subString.replace(reEscapeChar, "$1") : number || match);
          });
          return result;
        });
        module.exports = stringToPath;
      },
      8059: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isSymbol = __webpack_require__2(6596);
        var INFINITY = 1 / 0;
        function toKey(value) {
          if (typeof value == "string" || isSymbol(value)) {
            return value;
          }
          var result = value + "";
          return result == "0" && 1 / value == -INFINITY ? "-0" : result;
        }
        module.exports = toKey;
      },
      1543: (module) => {
        var funcProto = Function.prototype;
        var funcToString = funcProto.toString;
        function toSource(func) {
          if (func != null) {
            try {
              return funcToString.call(func);
            } catch (e) {}
            try {
              return func + "";
            } catch (e) {}
          }
          return "";
        }
        module.exports = toSource;
      },
      8882: (module) => {
        var reWhitespace = /\s/;
        function trimmedEndIndex(string) {
          var index = string.length;
          while (index-- && reWhitespace.test(string.charAt(index))) {}
          return index;
        }
        module.exports = trimmedEndIndex;
      },
      163: (module, __unused_webpack_exports, __webpack_require__2) => {
        var toInteger = __webpack_require__2(8007);
        var FUNC_ERROR_TEXT = "Expected a function";
        function before(n, func) {
          var result;
          if (typeof func != "function") {
            throw new TypeError(FUNC_ERROR_TEXT);
          }
          n = toInteger(n);
          return function() {
            if (--n > 0) {
              result = func.apply(this, arguments);
            }
            if (n <= 1) {
              func = undefined;
            }
            return result;
          };
        }
        module.exports = before;
      },
      63: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseClone = __webpack_require__2(1937);
        var CLONE_SYMBOLS_FLAG = 4;
        function clone(value) {
          return baseClone(value, CLONE_SYMBOLS_FLAG);
        }
        module.exports = clone;
      },
      7660: (module) => {
        function constant(value) {
          return function() {
            return value;
          };
        }
        module.exports = constant;
      },
      5757: (module, __unused_webpack_exports, __webpack_require__2) => {
        module.exports = __webpack_require__2(9760);
      },
      8330: (module) => {
        function eq(value, other) {
          return value === other || value !== value && other !== other;
        }
        module.exports = eq;
      },
      3131: (module, __unused_webpack_exports, __webpack_require__2) => {
        var escapeHtmlChar = __webpack_require__2(9025), toString = __webpack_require__2(7060);
        var reUnescapedHtml = /[&<>"']/g, reHasUnescapedHtml = RegExp(reUnescapedHtml.source);
        function escape(string) {
          string = toString(string);
          return string && reHasUnescapedHtml.test(string) ? string.replace(reUnescapedHtml, escapeHtmlChar) : string;
        }
        module.exports = escape;
      },
      9214: (module, __unused_webpack_exports, __webpack_require__2) => {
        var arrayFilter = __webpack_require__2(3928), baseFilter = __webpack_require__2(4384), baseIteratee = __webpack_require__2(7675), isArray = __webpack_require__2(2003);
        function filter(collection, predicate) {
          var func = isArray(collection) ? arrayFilter : baseFilter;
          return func(collection, baseIteratee(predicate, 3));
        }
        module.exports = filter;
      },
      4455: (module, __unused_webpack_exports, __webpack_require__2) => {
        var createFind = __webpack_require__2(7216), findIndex = __webpack_require__2(9339);
        var find = createFind(findIndex);
        module.exports = find;
      },
      9339: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseFindIndex = __webpack_require__2(6917), baseIteratee = __webpack_require__2(7675), toInteger = __webpack_require__2(8007);
        var nativeMax = Math.max;
        function findIndex(array, predicate, fromIndex) {
          var length = array == null ? 0 : array.length;
          if (!length) {
            return -1;
          }
          var index = fromIndex == null ? 0 : toInteger(fromIndex);
          if (index < 0) {
            index = nativeMax(length + index, 0);
          }
          return baseFindIndex(array, baseIteratee(predicate, 3), index);
        }
        module.exports = findIndex;
      },
      4176: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseFlatten = __webpack_require__2(4958);
        function flatten2(array) {
          var length = array == null ? 0 : array.length;
          return length ? baseFlatten(array, 1) : [];
        }
        module.exports = flatten2;
      },
      9760: (module, __unused_webpack_exports, __webpack_require__2) => {
        var arrayEach = __webpack_require__2(3643), baseEach = __webpack_require__2(7587), castFunction = __webpack_require__2(2072), isArray = __webpack_require__2(2003);
        function forEach(collection, iteratee) {
          var func = isArray(collection) ? arrayEach : baseEach;
          return func(collection, castFunction(iteratee));
        }
        module.exports = forEach;
      },
      1214: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseGet = __webpack_require__2(384);
        function get(object, path, defaultValue) {
          var result = object == null ? undefined : baseGet(object, path);
          return result === undefined ? defaultValue : result;
        }
        module.exports = get;
      },
      5930: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseHas = __webpack_require__2(8772), hasPath = __webpack_require__2(2248);
        function has(object, path) {
          return object != null && hasPath(object, path, baseHas);
        }
        module.exports = has;
      },
      8765: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseHasIn = __webpack_require__2(6571), hasPath = __webpack_require__2(2248);
        function hasIn(object, path) {
          return object != null && hasPath(object, path, baseHasIn);
        }
        module.exports = hasIn;
      },
      1686: (module) => {
        function identity(value) {
          return value;
        }
        module.exports = identity;
      },
      5193: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseIndexOf = __webpack_require__2(8357), isArrayLike = __webpack_require__2(6316), isString = __webpack_require__2(3085), toInteger = __webpack_require__2(8007), values = __webpack_require__2(2);
        var nativeMax = Math.max;
        function includes(collection, value, fromIndex, guard) {
          collection = isArrayLike(collection) ? collection : values(collection);
          fromIndex = fromIndex && !guard ? toInteger(fromIndex) : 0;
          var length = collection.length;
          if (fromIndex < 0) {
            fromIndex = nativeMax(length + fromIndex, 0);
          }
          return isString(collection) ? fromIndex <= length && collection.indexOf(value, fromIndex) > -1 : !!length && baseIndexOf(collection, value, fromIndex) > -1;
        }
        module.exports = includes;
      },
      4225: (module, __unused_webpack_exports, __webpack_require__2) => {
        var arrayMap = __webpack_require__2(14), baseIntersection = __webpack_require__2(739), baseRest = __webpack_require__2(3408), castArrayLikeObject = __webpack_require__2(9471);
        var intersection = baseRest(function(arrays) {
          var mapped = arrayMap(arrays, castArrayLikeObject);
          return mapped.length && mapped[0] === arrays[0] ? baseIntersection(mapped) : [];
        });
        module.exports = intersection;
      },
      2382: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseIsArguments = __webpack_require__2(2744), isObjectLike = __webpack_require__2(2620);
        var objectProto = Object.prototype;
        var hasOwnProperty = objectProto.hasOwnProperty;
        var propertyIsEnumerable = objectProto.propertyIsEnumerable;
        var isArguments = baseIsArguments(function() {
          return arguments;
        }()) ? baseIsArguments : function(value) {
          return isObjectLike(value) && hasOwnProperty.call(value, "callee") && !propertyIsEnumerable.call(value, "callee");
        };
        module.exports = isArguments;
      },
      2003: (module) => {
        var isArray = Array.isArray;
        module.exports = isArray;
      },
      6316: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isFunction = __webpack_require__2(8148), isLength = __webpack_require__2(7164);
        function isArrayLike(value) {
          return value != null && isLength(value.length) && !isFunction(value);
        }
        module.exports = isArrayLike;
      },
      1899: (module, __unused_webpack_exports, __webpack_require__2) => {
        var isArrayLike = __webpack_require__2(6316), isObjectLike = __webpack_require__2(2620);
        function isArrayLikeObject(value) {
          return isObjectLike(value) && isArrayLike(value);
        }
        module.exports = isArrayLikeObject;
      },
      1262: (module, exports, __webpack_require__2) => {
        module = __webpack_require__2.nmd(module);
        var root = __webpack_require__2(9107), stubFalse = __webpack_require__2(2125);
        var freeExports = exports && !exports.nodeType && exports;
        var freeModule = freeExports && true && module && !module.nodeType && module;
        var moduleExports = freeModule && freeModule.exports === freeExports;
        var Buffer = moduleExports ? root.Buffer : undefined;
        var nativeIsBuffer = Buffer ? Buffer.isBuffer : undefined;
        var isBuffer = nativeIsBuffer || stubFalse;
        module.exports = isBuffer;
      },
      8148: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseGetTag = __webpack_require__2(6522), isObject = __webpack_require__2(5603);
        var asyncTag = "[object AsyncFunction]", funcTag = "[object Function]", genTag = "[object GeneratorFunction]", proxyTag = "[object Proxy]";
        function isFunction(value) {
          if (!isObject(value)) {
            return false;
          }
          var tag = baseGetTag(value);
          return tag == funcTag || tag == genTag || tag == asyncTag || tag == proxyTag;
        }
        module.exports = isFunction;
      },
      7164: (module) => {
        var MAX_SAFE_INTEGER = 9007199254740991;
        function isLength(value) {
          return typeof value == "number" && value > -1 && value % 1 == 0 && value <= MAX_SAFE_INTEGER;
        }
        module.exports = isLength;
      },
      5652: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseIsMap = __webpack_require__2(8742), baseUnary = __webpack_require__2(2347), nodeUtil = __webpack_require__2(8315);
        var nodeIsMap = nodeUtil && nodeUtil.isMap;
        var isMap = nodeIsMap ? baseUnary(nodeIsMap) : baseIsMap;
        module.exports = isMap;
      },
      5603: (module) => {
        function isObject(value) {
          var type = typeof value;
          return value != null && (type == "object" || type == "function");
        }
        module.exports = isObject;
      },
      2620: (module) => {
        function isObjectLike(value) {
          return value != null && typeof value == "object";
        }
        module.exports = isObjectLike;
      },
      9318: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseIsSet = __webpack_require__2(5476), baseUnary = __webpack_require__2(2347), nodeUtil = __webpack_require__2(8315);
        var nodeIsSet = nodeUtil && nodeUtil.isSet;
        var isSet = nodeIsSet ? baseUnary(nodeIsSet) : baseIsSet;
        module.exports = isSet;
      },
      3085: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseGetTag = __webpack_require__2(6522), isArray = __webpack_require__2(2003), isObjectLike = __webpack_require__2(2620);
        var stringTag = "[object String]";
        function isString(value) {
          return typeof value == "string" || !isArray(value) && isObjectLike(value) && baseGetTag(value) == stringTag;
        }
        module.exports = isString;
      },
      6596: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseGetTag = __webpack_require__2(6522), isObjectLike = __webpack_require__2(2620);
        var symbolTag = "[object Symbol]";
        function isSymbol(value) {
          return typeof value == "symbol" || isObjectLike(value) && baseGetTag(value) == symbolTag;
        }
        module.exports = isSymbol;
      },
      9221: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseIsTypedArray = __webpack_require__2(5387), baseUnary = __webpack_require__2(2347), nodeUtil = __webpack_require__2(8315);
        var nodeIsTypedArray = nodeUtil && nodeUtil.isTypedArray;
        var isTypedArray = nodeIsTypedArray ? baseUnary(nodeIsTypedArray) : baseIsTypedArray;
        module.exports = isTypedArray;
      },
      5304: (module, __unused_webpack_exports, __webpack_require__2) => {
        var arrayLikeKeys = __webpack_require__2(7137), baseKeys = __webpack_require__2(6794), isArrayLike = __webpack_require__2(6316);
        function keys(object) {
          return isArrayLike(object) ? arrayLikeKeys(object) : baseKeys(object);
        }
        module.exports = keys;
      },
      7495: (module, __unused_webpack_exports, __webpack_require__2) => {
        var arrayLikeKeys = __webpack_require__2(7137), baseKeysIn = __webpack_require__2(8157), isArrayLike = __webpack_require__2(6316);
        function keysIn(object) {
          return isArrayLike(object) ? arrayLikeKeys(object, true) : baseKeysIn(object);
        }
        module.exports = keysIn;
      },
      6456: (module) => {
        function last2(array) {
          var length = array == null ? 0 : array.length;
          return length ? array[length - 1] : undefined;
        }
        module.exports = last2;
      },
      9513: (module, __unused_webpack_exports, __webpack_require__2) => {
        var MapCache = __webpack_require__2(3287);
        var FUNC_ERROR_TEXT = "Expected a function";
        function memoize(func, resolver) {
          if (typeof func != "function" || resolver != null && typeof resolver != "function") {
            throw new TypeError(FUNC_ERROR_TEXT);
          }
          var memoized = function() {
            var args = arguments, key = resolver ? resolver.apply(this, args) : args[0], cache = memoized.cache;
            if (cache.has(key)) {
              return cache.get(key);
            }
            var result = func.apply(this, args);
            memoized.cache = cache.set(key, result) || cache;
            return result;
          };
          memoized.cache = new (memoize.Cache || MapCache);
          return memoized;
        }
        memoize.Cache = MapCache;
        module.exports = memoize;
      },
      1700: (module) => {
        function noop() {}
        module.exports = noop;
      },
      8921: (module, __unused_webpack_exports, __webpack_require__2) => {
        var before = __webpack_require__2(163);
        function once(func) {
          return before(2, func);
        }
        module.exports = once;
      },
      7093: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseProperty = __webpack_require__2(1171), basePropertyDeep = __webpack_require__2(4589), isKey = __webpack_require__2(5456), toKey = __webpack_require__2(8059);
        function property(path) {
          return isKey(path) ? baseProperty(toKey(path)) : basePropertyDeep(path);
        }
        module.exports = property;
      },
      3281: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseFlatten = __webpack_require__2(4958), baseOrderBy = __webpack_require__2(3729), baseRest = __webpack_require__2(3408), isIterateeCall = __webpack_require__2(5934);
        var sortBy = baseRest(function(collection, iteratees) {
          if (collection == null) {
            return [];
          }
          var length = iteratees.length;
          if (length > 1 && isIterateeCall(collection, iteratees[0], iteratees[1])) {
            iteratees = [];
          } else if (length > 2 && isIterateeCall(iteratees[0], iteratees[1], iteratees[2])) {
            iteratees = [iteratees[0]];
          }
          return baseOrderBy(collection, baseFlatten(iteratees, 1), []);
        });
        module.exports = sortBy;
      },
      7013: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseClamp = __webpack_require__2(9631), baseToString = __webpack_require__2(8354), toInteger = __webpack_require__2(8007), toString = __webpack_require__2(7060);
        function startsWith(string, target, position) {
          string = toString(string);
          position = position == null ? 0 : baseClamp(toInteger(position), 0, string.length);
          target = baseToString(target);
          return string.slice(position, position + target.length) == target;
        }
        module.exports = startsWith;
      },
      119: (module) => {
        function stubArray() {
          return [];
        }
        module.exports = stubArray;
      },
      2125: (module) => {
        function stubFalse() {
          return false;
        }
        module.exports = stubFalse;
      },
      3950: (module, __unused_webpack_exports, __webpack_require__2) => {
        var toNumber = __webpack_require__2(3920);
        var INFINITY = 1 / 0, MAX_INTEGER = 179769313486231570000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000;
        function toFinite(value) {
          if (!value) {
            return value === 0 ? value : 0;
          }
          value = toNumber(value);
          if (value === INFINITY || value === -INFINITY) {
            var sign = value < 0 ? -1 : 1;
            return sign * MAX_INTEGER;
          }
          return value === value ? value : 0;
        }
        module.exports = toFinite;
      },
      8007: (module, __unused_webpack_exports, __webpack_require__2) => {
        var toFinite = __webpack_require__2(3950);
        function toInteger(value) {
          var result = toFinite(value), remainder = result % 1;
          return result === result ? remainder ? result - remainder : result : 0;
        }
        module.exports = toInteger;
      },
      3920: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseTrim = __webpack_require__2(9070), isObject = __webpack_require__2(5603), isSymbol = __webpack_require__2(6596);
        var NAN = 0 / 0;
        var reIsBadHex = /^[-+]0x[0-9a-f]+$/i;
        var reIsBinary = /^0b[01]+$/i;
        var reIsOctal = /^0o[0-7]+$/i;
        var freeParseInt = parseInt;
        function toNumber(value) {
          if (typeof value == "number") {
            return value;
          }
          if (isSymbol(value)) {
            return NAN;
          }
          if (isObject(value)) {
            var other = typeof value.valueOf == "function" ? value.valueOf() : value;
            value = isObject(other) ? other + "" : other;
          }
          if (typeof value != "string") {
            return value === 0 ? value : +value;
          }
          value = baseTrim(value);
          var isBinary = reIsBinary.test(value);
          return isBinary || reIsOctal.test(value) ? freeParseInt(value.slice(2), isBinary ? 2 : 8) : reIsBadHex.test(value) ? NAN : +value;
        }
        module.exports = toNumber;
      },
      7060: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseToString = __webpack_require__2(8354);
        function toString(value) {
          return value == null ? "" : baseToString(value);
        }
        module.exports = toString;
      },
      8496: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseIteratee = __webpack_require__2(7675), baseUniq = __webpack_require__2(7971);
        function uniqBy(array, iteratee) {
          return array && array.length ? baseUniq(array, baseIteratee(iteratee, 2)) : [];
        }
        module.exports = uniqBy;
      },
      2: (module, __unused_webpack_exports, __webpack_require__2) => {
        var baseValues = __webpack_require__2(4956), keys = __webpack_require__2(5304);
        function values(object) {
          return object == null ? [] : baseValues(object, keys(object));
        }
        module.exports = values;
      },
      8498: (__unused_webpack_module, exports) => {
        var __webpack_unused_export__;
        __webpack_unused_export__ = {
          value: true
        };
        exports.A = delay;
        function delay(time, value) {
          return new Promise(function(resolve) {
            setTimeout(function() {
              resolve(value);
            }, time);
          });
        }
      },
      1892: (module) => {
        function hasOwnProperty(obj, prop) {
          return Object.prototype.hasOwnProperty.call(obj, prop);
        }
        module.exports = function(qs, sep, eq, options) {
          sep = sep || "&";
          eq = eq || "=";
          var obj = {};
          if (typeof qs !== "string" || qs.length === 0) {
            return obj;
          }
          var regexp = /\+/g;
          qs = qs.split(sep);
          var maxKeys = 1000;
          if (options && typeof options.maxKeys === "number") {
            maxKeys = options.maxKeys;
          }
          var len = qs.length;
          if (maxKeys > 0 && len > maxKeys) {
            len = maxKeys;
          }
          for (var i = 0;i < len; ++i) {
            var x = qs[i].replace(regexp, "%20"), idx = x.indexOf(eq), kstr, vstr, k, v;
            if (idx >= 0) {
              kstr = x.substr(0, idx);
              vstr = x.substr(idx + 1);
            } else {
              kstr = x;
              vstr = "";
            }
            k = decodeURIComponent(kstr);
            v = decodeURIComponent(vstr);
            if (!hasOwnProperty(obj, k)) {
              obj[k] = v;
            } else if (isArray(obj[k])) {
              obj[k].push(v);
            } else {
              obj[k] = [obj[k], v];
            }
          }
          return obj;
        };
        var isArray = Array.isArray || function(xs) {
          return Object.prototype.toString.call(xs) === "[object Array]";
        };
      },
      5052: (module) => {
        var stringifyPrimitive = function(v) {
          switch (typeof v) {
            case "string":
              return v;
            case "boolean":
              return v ? "true" : "false";
            case "number":
              return isFinite(v) ? v : "";
            default:
              return "";
          }
        };
        module.exports = function(obj, sep, eq, name) {
          sep = sep || "&";
          eq = eq || "=";
          if (obj === null) {
            obj = undefined;
          }
          if (typeof obj === "object") {
            return map(objectKeys(obj), function(k) {
              var ks = encodeURIComponent(stringifyPrimitive(k)) + eq;
              if (isArray(obj[k])) {
                return map(obj[k], function(v) {
                  return ks + encodeURIComponent(stringifyPrimitive(v));
                }).join(sep);
              } else {
                return ks + encodeURIComponent(stringifyPrimitive(obj[k]));
              }
            }).join(sep);
          }
          if (!name)
            return "";
          return encodeURIComponent(stringifyPrimitive(name)) + eq + encodeURIComponent(stringifyPrimitive(obj));
        };
        var isArray = Array.isArray || function(xs) {
          return Object.prototype.toString.call(xs) === "[object Array]";
        };
        function map(xs, f) {
          if (xs.map)
            return xs.map(f);
          var res = [];
          for (var i = 0;i < xs.length; i++) {
            res.push(f(xs[i], i));
          }
          return res;
        }
        var objectKeys = Object.keys || function(obj) {
          var res = [];
          for (var key in obj) {
            if (Object.prototype.hasOwnProperty.call(obj, key))
              res.push(key);
          }
          return res;
        };
      },
      6448: (__unused_webpack_module, exports, __webpack_require__2) => {
        exports.decode = exports.parse = __webpack_require__2(1892);
        exports.encode = exports.stringify = __webpack_require__2(5052);
      },
      6046: (module) => {
        var symbolExists = typeof Symbol !== "undefined";
        var protocols = {
          iterator: symbolExists ? Symbol.iterator : "@@iterator"
        };
        function throwProtocolError(name, coll) {
          throw new Error("don't know how to " + name + " collection: " + coll);
        }
        function fulfillsProtocol(obj, name) {
          if (name === "iterator") {
            return obj[protocols.iterator] || obj.next;
          }
          return obj[protocols[name]];
        }
        function getProtocolProperty(obj, name) {
          return obj[protocols[name]];
        }
        function iterator(coll) {
          var iter = getProtocolProperty(coll, "iterator");
          if (iter) {
            return iter.call(coll);
          } else if (coll.next) {
            return coll;
          } else if (isArray(coll)) {
            return new ArrayIterator(coll);
          } else if (isObject(coll)) {
            return new ObjectIterator(coll);
          }
        }
        function ArrayIterator(arr) {
          this.arr = arr;
          this.index = 0;
        }
        ArrayIterator.prototype.next = function() {
          if (this.index < this.arr.length) {
            return {
              value: this.arr[this.index++],
              done: false
            };
          }
          return {
            done: true
          };
        };
        function ObjectIterator(obj) {
          this.obj = obj;
          this.keys = Object.keys(obj);
          this.index = 0;
        }
        ObjectIterator.prototype.next = function() {
          if (this.index < this.keys.length) {
            var k = this.keys[this.index++];
            return {
              value: [k, this.obj[k]],
              done: false
            };
          }
          return {
            done: true
          };
        };
        var toString = Object.prototype.toString;
        var isArray = typeof Array.isArray === "function" ? Array.isArray : function(obj) {
          return toString.call(obj) == "[object Array]";
        };
        function isFunction(x) {
          return typeof x === "function";
        }
        function isObject(x) {
          return x instanceof Object && Object.getPrototypeOf(x) === Object.getPrototypeOf({});
        }
        function isNumber(x) {
          return typeof x === "number";
        }
        function Reduced(value) {
          this["@@transducer/reduced"] = true;
          this["@@transducer/value"] = value;
        }
        function isReduced(x) {
          return x instanceof Reduced || x && x["@@transducer/reduced"];
        }
        function deref(x) {
          return x["@@transducer/value"];
        }
        function ensureReduced(val) {
          if (isReduced(val)) {
            return val;
          } else {
            return new Reduced(val);
          }
        }
        function ensureUnreduced(v) {
          if (isReduced(v)) {
            return deref(v);
          } else {
            return v;
          }
        }
        function reduce(coll, xform, init) {
          if (isArray(coll)) {
            var result = init;
            var index = -1;
            var len = coll.length;
            while (++index < len) {
              result = xform["@@transducer/step"](result, coll[index]);
              if (isReduced(result)) {
                result = deref(result);
                break;
              }
            }
            return xform["@@transducer/result"](result);
          } else if (isObject(coll) || fulfillsProtocol(coll, "iterator")) {
            var result = init;
            var iter = iterator(coll);
            var val = iter.next();
            while (!val.done) {
              result = xform["@@transducer/step"](result, val.value);
              if (isReduced(result)) {
                result = deref(result);
                break;
              }
              val = iter.next();
            }
            return xform["@@transducer/result"](result);
          }
          throwProtocolError("iterate", coll);
        }
        function transduce(coll, xform, reducer, init) {
          xform = xform(reducer);
          if (init === undefined) {
            init = xform["@@transducer/init"]();
          }
          return reduce(coll, xform, init);
        }
        function compose() {
          var funcs = Array.prototype.slice.call(arguments);
          return function(r) {
            var value = r;
            for (var i = funcs.length - 1;i >= 0; i--) {
              value = funcs[i](value);
            }
            return value;
          };
        }
        function transformer(f) {
          var t2 = {};
          t2["@@transducer/init"] = function() {
            throw new Error("init value unavailable");
          };
          t2["@@transducer/result"] = function(v) {
            return v;
          };
          t2["@@transducer/step"] = f;
          return t2;
        }
        function bound(f, ctx, count) {
          count = count != null ? count : 1;
          if (!ctx) {
            return f;
          } else {
            switch (count) {
              case 1:
                return function(x) {
                  return f.call(ctx, x);
                };
              case 2:
                return function(x, y) {
                  return f.call(ctx, x, y);
                };
              default:
                return f.bind(ctx);
            }
          }
        }
        function arrayMap(arr, f, ctx) {
          var index = -1;
          var length = arr.length;
          var result = Array(length);
          f = bound(f, ctx, 2);
          while (++index < length) {
            result[index] = f(arr[index], index);
          }
          return result;
        }
        function arrayFilter(arr, f, ctx) {
          var len = arr.length;
          var result = [];
          f = bound(f, ctx, 2);
          for (var i = 0;i < len; i++) {
            if (f(arr[i], i)) {
              result.push(arr[i]);
            }
          }
          return result;
        }
        function Map2(f, xform) {
          this.xform = xform;
          this.f = f;
        }
        Map2.prototype["@@transducer/init"] = function() {
          return this.xform["@@transducer/init"]();
        };
        Map2.prototype["@@transducer/result"] = function(v) {
          return this.xform["@@transducer/result"](v);
        };
        Map2.prototype["@@transducer/step"] = function(res, input) {
          return this.xform["@@transducer/step"](res, this.f(input));
        };
        function map(coll, f, ctx) {
          if (isFunction(coll)) {
            ctx = f;
            f = coll;
            coll = null;
          }
          f = bound(f, ctx);
          if (coll) {
            if (isArray(coll)) {
              return arrayMap(coll, f, ctx);
            }
            return seq(coll, map(f));
          }
          return function(xform) {
            return new Map2(f, xform);
          };
        }
        function Filter(f, xform) {
          this.xform = xform;
          this.f = f;
        }
        Filter.prototype["@@transducer/init"] = function() {
          return this.xform["@@transducer/init"]();
        };
        Filter.prototype["@@transducer/result"] = function(v) {
          return this.xform["@@transducer/result"](v);
        };
        Filter.prototype["@@transducer/step"] = function(res, input) {
          if (this.f(input)) {
            return this.xform["@@transducer/step"](res, input);
          }
          return res;
        };
        function filter(coll, f, ctx) {
          if (isFunction(coll)) {
            ctx = f;
            f = coll;
            coll = null;
          }
          f = bound(f, ctx);
          if (coll) {
            if (isArray(coll)) {
              return arrayFilter(coll, f, ctx);
            }
            return seq(coll, filter(f));
          }
          return function(xform) {
            return new Filter(f, xform);
          };
        }
        function remove(coll, f, ctx) {
          if (isFunction(coll)) {
            ctx = f;
            f = coll;
            coll = null;
          }
          f = bound(f, ctx);
          return filter(coll, function(x) {
            return !f(x);
          });
        }
        function keep(coll) {
          return filter(coll, function(x) {
            return x != null;
          });
        }
        function Dedupe(xform) {
          this.xform = xform;
          this.last = undefined;
        }
        Dedupe.prototype["@@transducer/init"] = function() {
          return this.xform["@@transducer/init"]();
        };
        Dedupe.prototype["@@transducer/result"] = function(v) {
          return this.xform["@@transducer/result"](v);
        };
        Dedupe.prototype["@@transducer/step"] = function(result, input) {
          if (input !== this.last) {
            this.last = input;
            return this.xform["@@transducer/step"](result, input);
          }
          return result;
        };
        function dedupe(coll) {
          if (coll) {
            return seq(coll, dedupe());
          }
          return function(xform) {
            return new Dedupe(xform);
          };
        }
        function TakeWhile(f, xform) {
          this.xform = xform;
          this.f = f;
        }
        TakeWhile.prototype["@@transducer/init"] = function() {
          return this.xform["@@transducer/init"]();
        };
        TakeWhile.prototype["@@transducer/result"] = function(v) {
          return this.xform["@@transducer/result"](v);
        };
        TakeWhile.prototype["@@transducer/step"] = function(result, input) {
          if (this.f(input)) {
            return this.xform["@@transducer/step"](result, input);
          }
          return new Reduced(result);
        };
        function takeWhile(coll, f, ctx) {
          if (isFunction(coll)) {
            ctx = f;
            f = coll;
            coll = null;
          }
          f = bound(f, ctx);
          if (coll) {
            return seq(coll, takeWhile(f));
          }
          return function(xform) {
            return new TakeWhile(f, xform);
          };
        }
        function Take(n, xform) {
          this.n = n;
          this.i = 0;
          this.xform = xform;
        }
        Take.prototype["@@transducer/init"] = function() {
          return this.xform["@@transducer/init"]();
        };
        Take.prototype["@@transducer/result"] = function(v) {
          return this.xform["@@transducer/result"](v);
        };
        Take.prototype["@@transducer/step"] = function(result, input) {
          if (this.i < this.n) {
            result = this.xform["@@transducer/step"](result, input);
            if (this.i + 1 >= this.n) {
              result = ensureReduced(result);
            }
          }
          this.i++;
          return result;
        };
        function take(coll, n) {
          if (isNumber(coll)) {
            n = coll;
            coll = null;
          }
          if (coll) {
            return seq(coll, take(n));
          }
          return function(xform) {
            return new Take(n, xform);
          };
        }
        function Drop(n, xform) {
          this.n = n;
          this.i = 0;
          this.xform = xform;
        }
        Drop.prototype["@@transducer/init"] = function() {
          return this.xform["@@transducer/init"]();
        };
        Drop.prototype["@@transducer/result"] = function(v) {
          return this.xform["@@transducer/result"](v);
        };
        Drop.prototype["@@transducer/step"] = function(result, input) {
          if (this.i++ < this.n) {
            return result;
          }
          return this.xform["@@transducer/step"](result, input);
        };
        function drop(coll, n) {
          if (isNumber(coll)) {
            n = coll;
            coll = null;
          }
          if (coll) {
            return seq(coll, drop(n));
          }
          return function(xform) {
            return new Drop(n, xform);
          };
        }
        function DropWhile(f, xform) {
          this.xform = xform;
          this.f = f;
          this.dropping = true;
        }
        DropWhile.prototype["@@transducer/init"] = function() {
          return this.xform["@@transducer/init"]();
        };
        DropWhile.prototype["@@transducer/result"] = function(v) {
          return this.xform["@@transducer/result"](v);
        };
        DropWhile.prototype["@@transducer/step"] = function(result, input) {
          if (this.dropping) {
            if (this.f(input)) {
              return result;
            } else {
              this.dropping = false;
            }
          }
          return this.xform["@@transducer/step"](result, input);
        };
        function dropWhile(coll, f, ctx) {
          if (isFunction(coll)) {
            ctx = f;
            f = coll;
            coll = null;
          }
          f = bound(f, ctx);
          if (coll) {
            return seq(coll, dropWhile(f));
          }
          return function(xform) {
            return new DropWhile(f, xform);
          };
        }
        function Partition(n, xform) {
          this.n = n;
          this.i = 0;
          this.xform = xform;
          this.part = new Array(n);
        }
        Partition.prototype["@@transducer/init"] = function() {
          return this.xform["@@transducer/init"]();
        };
        Partition.prototype["@@transducer/result"] = function(v) {
          if (this.i > 0) {
            return ensureUnreduced(this.xform["@@transducer/step"](v, this.part.slice(0, this.i)));
          }
          return this.xform["@@transducer/result"](v);
        };
        Partition.prototype["@@transducer/step"] = function(result, input) {
          this.part[this.i] = input;
          this.i += 1;
          if (this.i === this.n) {
            var out = this.part.slice(0, this.n);
            this.part = new Array(this.n);
            this.i = 0;
            return this.xform["@@transducer/step"](result, out);
          }
          return result;
        };
        function partition(coll, n) {
          if (isNumber(coll)) {
            n = coll;
            coll = null;
          }
          if (coll) {
            return seq(coll, partition(n));
          }
          return function(xform) {
            return new Partition(n, xform);
          };
        }
        var NOTHING = {};
        function PartitionBy(f, xform) {
          this.f = f;
          this.xform = xform;
          this.part = [];
          this.last = NOTHING;
        }
        PartitionBy.prototype["@@transducer/init"] = function() {
          return this.xform["@@transducer/init"]();
        };
        PartitionBy.prototype["@@transducer/result"] = function(v) {
          var l = this.part.length;
          if (l > 0) {
            return ensureUnreduced(this.xform["@@transducer/step"](v, this.part.slice(0, l)));
          }
          return this.xform["@@transducer/result"](v);
        };
        PartitionBy.prototype["@@transducer/step"] = function(result, input) {
          var current = this.f(input);
          if (current === this.last || this.last === NOTHING) {
            this.part.push(input);
          } else {
            result = this.xform["@@transducer/step"](result, this.part);
            this.part = [input];
          }
          this.last = current;
          return result;
        };
        function partitionBy(coll, f, ctx) {
          if (isFunction(coll)) {
            ctx = f;
            f = coll;
            coll = null;
          }
          f = bound(f, ctx);
          if (coll) {
            return seq(coll, partitionBy(f));
          }
          return function(xform) {
            return new PartitionBy(f, xform);
          };
        }
        function Interpose(sep, xform) {
          this.sep = sep;
          this.xform = xform;
          this.started = false;
        }
        Interpose.prototype["@@transducer/init"] = function() {
          return this.xform["@@transducer/init"]();
        };
        Interpose.prototype["@@transducer/result"] = function(v) {
          return this.xform["@@transducer/result"](v);
        };
        Interpose.prototype["@@transducer/step"] = function(result, input) {
          if (this.started) {
            var withSep = this.xform["@@transducer/step"](result, this.sep);
            if (isReduced(withSep)) {
              return withSep;
            } else {
              return this.xform["@@transducer/step"](withSep, input);
            }
          } else {
            this.started = true;
            return this.xform["@@transducer/step"](result, input);
          }
        };
        function interpose(coll, separator) {
          if (arguments.length === 1) {
            separator = coll;
            return function(xform) {
              return new Interpose(separator, xform);
            };
          }
          return seq(coll, interpose(separator));
        }
        function Repeat(n, xform) {
          this.xform = xform;
          this.n = n;
        }
        Repeat.prototype["@@transducer/init"] = function() {
          return this.xform["@@transducer/init"]();
        };
        Repeat.prototype["@@transducer/result"] = function(v) {
          return this.xform["@@transducer/result"](v);
        };
        Repeat.prototype["@@transducer/step"] = function(result, input) {
          var n = this.n;
          var r = result;
          for (var i = 0;i < n; i++) {
            r = this.xform["@@transducer/step"](r, input);
            if (isReduced(r)) {
              break;
            }
          }
          return r;
        };
        function repeat(coll, n) {
          if (arguments.length === 1) {
            n = coll;
            return function(xform) {
              return new Repeat(n, xform);
            };
          }
          return seq(coll, repeat(n));
        }
        function TakeNth(n, xform) {
          this.xform = xform;
          this.n = n;
          this.i = -1;
        }
        TakeNth.prototype["@@transducer/init"] = function() {
          return this.xform["@@transducer/init"]();
        };
        TakeNth.prototype["@@transducer/result"] = function(v) {
          return this.xform["@@transducer/result"](v);
        };
        TakeNth.prototype["@@transducer/step"] = function(result, input) {
          this.i += 1;
          if (this.i % this.n === 0) {
            return this.xform["@@transducer/step"](result, input);
          }
          return result;
        };
        function takeNth(coll, nth) {
          if (arguments.length === 1) {
            nth = coll;
            return function(xform) {
              return new TakeNth(nth, xform);
            };
          }
          return seq(coll, takeNth(nth));
        }
        function Cat(xform) {
          this.xform = xform;
        }
        Cat.prototype["@@transducer/init"] = function() {
          return this.xform["@@transducer/init"]();
        };
        Cat.prototype["@@transducer/result"] = function(v) {
          return this.xform["@@transducer/result"](v);
        };
        Cat.prototype["@@transducer/step"] = function(result, input) {
          var xform = this.xform;
          var newxform = {};
          newxform["@@transducer/init"] = function() {
            return xform["@@transducer/init"]();
          };
          newxform["@@transducer/result"] = function(v) {
            return v;
          };
          newxform["@@transducer/step"] = function(result2, input2) {
            var val = xform["@@transducer/step"](result2, input2);
            return isReduced(val) ? deref(val) : val;
          };
          return reduce(input, newxform, result);
        };
        function cat(xform) {
          return new Cat(xform);
        }
        function mapcat(f, ctx) {
          f = bound(f, ctx);
          return compose(map(f), cat);
        }
        function push(arr, x) {
          arr.push(x);
          return arr;
        }
        function merge(obj, x) {
          if (isArray(x) && x.length === 2) {
            obj[x[0]] = x[1];
          } else {
            var keys = Object.keys(x);
            var len = keys.length;
            for (var i = 0;i < len; i++) {
              obj[keys[i]] = x[keys[i]];
            }
          }
          return obj;
        }
        var arrayReducer = {};
        arrayReducer["@@transducer/init"] = function() {
          return [];
        };
        arrayReducer["@@transducer/result"] = function(v) {
          return v;
        };
        arrayReducer["@@transducer/step"] = push;
        var objReducer = {};
        objReducer["@@transducer/init"] = function() {
          return {};
        };
        objReducer["@@transducer/result"] = function(v) {
          return v;
        };
        objReducer["@@transducer/step"] = merge;
        function toArray(coll, xform) {
          if (!xform) {
            return reduce(coll, arrayReducer, []);
          }
          return transduce(coll, xform, arrayReducer, []);
        }
        function toObj(coll, xform) {
          if (!xform) {
            return reduce(coll, objReducer, {});
          }
          return transduce(coll, xform, objReducer, {});
        }
        function toIter(coll, xform) {
          if (!xform) {
            return iterator(coll);
          }
          return new LazyTransformer(xform, coll);
        }
        function seq(coll, xform) {
          if (isArray(coll)) {
            return transduce(coll, xform, arrayReducer, []);
          } else if (isObject(coll)) {
            return transduce(coll, xform, objReducer, {});
          } else if (coll["@@transducer/step"]) {
            var init;
            if (coll["@@transducer/init"]) {
              init = coll["@@transducer/init"]();
            } else {
              init = new coll.constructor;
            }
            return transduce(coll, xform, coll, init);
          } else if (fulfillsProtocol(coll, "iterator")) {
            return new LazyTransformer(xform, coll);
          }
          throwProtocolError("sequence", coll);
        }
        function into(to, xform, from) {
          if (isArray(to)) {
            return transduce(from, xform, arrayReducer, to);
          } else if (isObject(to)) {
            return transduce(from, xform, objReducer, to);
          } else if (to["@@transducer/step"]) {
            return transduce(from, xform, to, to);
          }
          throwProtocolError("into", to);
        }
        var stepper = {};
        stepper["@@transducer/result"] = function(v) {
          return isReduced(v) ? deref(v) : v;
        };
        stepper["@@transducer/step"] = function(lt, x) {
          lt.items.push(x);
          return lt.rest;
        };
        function Stepper(xform, iter) {
          this.xform = xform(stepper);
          this.iter = iter;
        }
        Stepper.prototype["@@transducer/step"] = function(lt) {
          var len = lt.items.length;
          while (lt.items.length === len) {
            var n = this.iter.next();
            if (n.done || isReduced(n.value)) {
              this.xform["@@transducer/result"](this);
              break;
            }
            this.xform["@@transducer/step"](lt, n.value);
          }
        };
        function LazyTransformer(xform, coll) {
          this.iter = iterator(coll);
          this.items = [];
          this.stepper = new Stepper(xform, iterator(coll));
        }
        LazyTransformer.prototype[protocols.iterator] = function() {
          return this;
        };
        LazyTransformer.prototype.next = function() {
          this["@@transducer/step"]();
          if (this.items.length) {
            return {
              value: this.items.pop(),
              done: false
            };
          } else {
            return { done: true };
          }
        };
        LazyTransformer.prototype["@@transducer/step"] = function() {
          if (!this.items.length) {
            this.stepper["@@transducer/step"](this);
          }
        };
        function range(n) {
          var arr = new Array(n);
          for (var i = 0;i < arr.length; i++) {
            arr[i] = i;
          }
          return arr;
        }
        module.exports = {
          reduce,
          transformer,
          Reduced,
          isReduced,
          iterator,
          push,
          merge,
          transduce,
          seq,
          toArray,
          toObj,
          toIter,
          into,
          compose,
          map,
          filter,
          remove,
          cat,
          mapcat,
          keep,
          dedupe,
          take,
          takeWhile,
          takeNth,
          drop,
          dropWhile,
          partition,
          partitionBy,
          interpose,
          repeat,
          range,
          LazyTransformer
        };
      },
      7332: (__unused_webpack_module, exports, __webpack_require__2) => {
        var __webpack_unused_export__;
        __webpack_unused_export__ = {
          value: true
        };
        exports.defn = defn;
        __webpack_unused_export__ = defobj;
        __webpack_unused_export__ = defonce;
        __webpack_unused_export__ = markReloadable;
        var range = __webpack_require__2(9060);
        var zipObject = __webpack_require__2(2118);
        var moduleUsedUdKeys = new WeakMap;
        function markReloadable(module) {
          if (module.hot) {
            module.hot.accept();
          }
        }
        function defonce(module, fn) {
          var key = arguments.length > 2 && arguments[2] !== undefined ? arguments[2] : "";
          markReloadable(module);
          var usedKeys = moduleUsedUdKeys.get(module);
          if (!usedKeys) {
            usedKeys = new Set;
            moduleUsedUdKeys.set(module, usedKeys);
          }
          if (usedKeys.has(key)) {
            throw new Error("ud functions can only be used once per module with a given key");
          }
          usedKeys.add(key);
          var valueWasSet = false;
          var value = undefined;
          if (module.hot) {
            if (module.hot.data && module.hot.data.__ud__ && Object.prototype.hasOwnProperty.call(module.hot.data.__ud__, key)) {
              value = module.hot.data.__ud__[key];
              valueWasSet = true;
            }
            module.hot.dispose(function(data) {
              if (!data.__ud__)
                data.__ud__ = {};
              data.__ud__[key] = value;
            });
          }
          if (!valueWasSet)
            value = fn();
          return value;
        }
        function defobj(module, object) {
          var key = arguments.length > 2 && arguments[2] !== undefined ? arguments[2] : "";
          var sharedObject = defonce(module, function() {
            return object;
          }, "--defobj-" + key);
          if (sharedObject !== object) {
            cloneOntoTarget(sharedObject, object);
          }
          return sharedObject;
        }
        function cloneOntoTarget(target, object) {
          Object.getOwnPropertyNames(target).filter(function(name) {
            return !Object.prototype.hasOwnProperty.call(object, name);
          }).forEach(function(name) {
            delete target[name];
          });
          var newPropsChain = Object.getOwnPropertyNames(object);
          Object.defineProperties(target, zipObject(newPropsChain, newPropsChain.map(function(name) {
            return Object.getOwnPropertyDescriptor(object, name);
          }).filter(Boolean).map(function(_ref) {
            var { value, enumerable } = _ref;
            return {
              value,
              enumerable,
              writable: true,
              configurable: true
            };
          })));
          return target;
        }
        function defn(module, fn) {
          var key = arguments.length > 2 && arguments[2] !== undefined ? arguments[2] : "";
          var shared = defonce(module, function() {
            if (!module.hot) {
              return {
                fn: null,
                wrapper: fn
              };
            }
            var shared2 = {
              fn: null,
              wrapper: null
            };
            var paramsList = range(fn.length).map(function(x) {
              return "a" + x;
            }).join(",");
            shared2.wrapper = new Function("shared", `
      'use strict';
      return function `.concat(fn.name, "__ud_wrapper(").concat(paramsList, `) {
        if (new.target) {
          return Reflect.construct(shared.fn, arguments, new.target);
        } else {
          return shared.fn.apply(this, arguments);
        }
      };
      `))(shared2);
            if (fn.prototype) {
              shared2.wrapper.prototype = Object.create(fn.prototype);
              shared2.wrapper.prototype.constructor = shared2.wrapper;
            } else {
              shared2.wrapper.prototype = fn.prototype;
            }
            return shared2;
          }, "--defn-shared-" + key);
          shared.fn = fn;
          if (module.hot) {
            if (fn.prototype && shared.wrapper.prototype && Object.getPrototypeOf(shared.wrapper.prototype) !== fn.prototype) {
              Object.setPrototypeOf(shared.wrapper.prototype, fn.prototype);
            }
            Object.setPrototypeOf(shared.wrapper, fn);
          }
          return shared.wrapper;
        }
      },
      2118: (module) => {
        var zipObject = function(keys, values) {
          if (arguments.length == 1) {
            values = keys[1];
            keys = keys[0];
          }
          var result = {};
          var i = 0;
          for (i;i < keys.length; i += 1) {
            result[keys[i]] = values[i];
          }
          return result;
        };
        module.exports = zipObject;
      },
      8915: (module) => {
        function _arrayLikeToArray(arr, len) {
          if (len == null || len > arr.length)
            len = arr.length;
          for (var i = 0, arr2 = new Array(len);i < len; i++)
            arr2[i] = arr[i];
          return arr2;
        }
        module.exports = _arrayLikeToArray, module.exports.__esModule = true, module.exports["default"] = module.exports;
      },
      4233: (module, __unused_webpack_exports, __webpack_require__2) => {
        var arrayLikeToArray = __webpack_require__2(8915);
        function _arrayWithoutHoles(arr) {
          if (Array.isArray(arr))
            return arrayLikeToArray(arr);
        }
        module.exports = _arrayWithoutHoles, module.exports.__esModule = true, module.exports["default"] = module.exports;
      },
      1654: (module) => {
        function _interopRequireDefault(obj) {
          return obj && obj.__esModule ? obj : {
            default: obj
          };
        }
        module.exports = _interopRequireDefault, module.exports.__esModule = true, module.exports["default"] = module.exports;
      },
      6135: (module) => {
        function _iterableToArray(iter) {
          if (typeof Symbol !== "undefined" && iter[Symbol.iterator] != null || iter["@@iterator"] != null)
            return Array.from(iter);
        }
        module.exports = _iterableToArray, module.exports.__esModule = true, module.exports["default"] = module.exports;
      },
      2449: (module) => {
        function _nonIterableSpread() {
          throw new TypeError(`Invalid attempt to spread non-iterable instance.
In order to be iterable, non-array objects must have a [Symbol.iterator]() method.`);
        }
        module.exports = _nonIterableSpread, module.exports.__esModule = true, module.exports["default"] = module.exports;
      },
      1752: (module, __unused_webpack_exports, __webpack_require__2) => {
        var arrayWithoutHoles = __webpack_require__2(4233);
        var iterableToArray = __webpack_require__2(6135);
        var unsupportedIterableToArray = __webpack_require__2(6030);
        var nonIterableSpread = __webpack_require__2(2449);
        function _toConsumableArray(arr) {
          return arrayWithoutHoles(arr) || iterableToArray(arr) || unsupportedIterableToArray(arr) || nonIterableSpread();
        }
        module.exports = _toConsumableArray, module.exports.__esModule = true, module.exports["default"] = module.exports;
      },
      2990: (module) => {
        function _typeof(o) {
          "@babel/helpers - typeof";
          return module.exports = _typeof = typeof Symbol == "function" && typeof Symbol.iterator == "symbol" ? function(o2) {
            return typeof o2;
          } : function(o2) {
            return o2 && typeof Symbol == "function" && o2.constructor === Symbol && o2 !== Symbol.prototype ? "symbol" : typeof o2;
          }, module.exports.__esModule = true, module.exports["default"] = module.exports, _typeof(o);
        }
        module.exports = _typeof, module.exports.__esModule = true, module.exports["default"] = module.exports;
      },
      6030: (module, __unused_webpack_exports, __webpack_require__2) => {
        var arrayLikeToArray = __webpack_require__2(8915);
        function _unsupportedIterableToArray(o, minLen) {
          if (!o)
            return;
          if (typeof o === "string")
            return arrayLikeToArray(o, minLen);
          var n = Object.prototype.toString.call(o).slice(8, -1);
          if (n === "Object" && o.constructor)
            n = o.constructor.name;
          if (n === "Map" || n === "Set")
            return Array.from(o);
          if (n === "Arguments" || /^(?:Ui|I)nt(?:8|16|32)(?:Clamped)?Array$/.test(n))
            return arrayLikeToArray(o, minLen);
        }
        module.exports = _unsupportedIterableToArray, module.exports.__esModule = true, module.exports["default"] = module.exports;
      }
    };
    var __webpack_module_cache__ = {};
    function __webpack_require__(moduleId) {
      var cachedModule = __webpack_module_cache__[moduleId];
      if (cachedModule !== undefined) {
        return cachedModule.exports;
      }
      var module = __webpack_module_cache__[moduleId] = {
        id: moduleId,
        loaded: false,
        exports: {}
      };
      __webpack_modules__[moduleId].call(module.exports, module, module.exports, __webpack_require__);
      module.loaded = true;
      return module.exports;
    }
    (() => {
      __webpack_require__.amdD = function() {
        throw new Error("define cannot be used indirect");
      };
    })();
    (() => {
      __webpack_require__.amdO = {};
    })();
    (() => {
      __webpack_require__.n = (module) => {
        var getter = module && module.__esModule ? () => module["default"] : () => module;
        __webpack_require__.d(getter, { a: getter });
        return getter;
      };
    })();
    (() => {
      __webpack_require__.d = (exports, definition) => {
        for (var key in definition) {
          if (__webpack_require__.o(definition, key) && !__webpack_require__.o(exports, key)) {
            Object.defineProperty(exports, key, { enumerable: true, get: definition[key] });
          }
        }
      };
    })();
    (() => {
      __webpack_require__.g = function() {
        if (typeof globalThis === "object")
          return globalThis;
        try {
          return this || new Function("return this")();
        } catch (e) {
          if (typeof window === "object")
            return window;
        }
      }();
    })();
    (() => {
      __webpack_require__.hmd = (module) => {
        module = Object.create(module);
        if (!module.children)
          module.children = [];
        Object.defineProperty(module, "exports", {
          enumerable: true,
          set: () => {
            throw new Error("ES Modules may not assign module.exports or exports.*, Use ESM export syntax, instead: " + module.id);
          }
        });
        return module;
      };
    })();
    (() => {
      __webpack_require__.o = (obj, prop) => Object.prototype.hasOwnProperty.call(obj, prop);
    })();
    (() => {
      __webpack_require__.r = (exports) => {
        if (typeof Symbol !== "undefined" && Symbol.toStringTag) {
          Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
        }
        Object.defineProperty(exports, "__esModule", { value: true });
      };
    })();
    (() => {
      __webpack_require__.nmd = (module) => {
        module.paths = [];
        if (!module.children)
          module.children = [];
        return module;
      };
    })();
    var __webpack_exports__ = {};
    (() => {
      const pageOrigin = document.location.origin;
      if (pageOrigin !== "https://mail.google.com") {
        throw new Error("Should not happen: InboxSDK pageWorld.js running in document that didn't request it.");
      }
      if (!document.head?.hasAttribute("data-inboxsdk-script-injected")) {
        throw new Error("Should not happen: InboxSDK pageWorld.js running in document that didn't request it.");
      }
      if (!__webpack_require__.g.__InboxSDKInjected) {
        __webpack_require__.g.__InboxSDKInjected = true;
        const logger = __webpack_require__(4530);
        let oldDefine;
        try {
          if (__webpack_require__.amdD && __webpack_require__.amdO) {
            oldDefine = __webpack_require__.amdD;
            __webpack_require__.amdD = null;
          }
          const extCorbWorkaroundPageWorld = __webpack_require__(4835);
          const xhrHelper = __webpack_require__(284).A;
          const setupDataExposer = __webpack_require__(6465).A;
          const setupEventReemitter = __webpack_require__(9729).A;
          const setupErrorSilencer = __webpack_require__(5915).A;
          const setupCustomViewEventAssassin = __webpack_require__(4630).A;
          const setupPushStateListener = __webpack_require__(3095).A;
          const setupInboxCustomViewLinkFixer = __webpack_require__(9234).A;
          const gmailInterceptor = __webpack_require__(5691).A;
          const setupGmonkeyHandler = __webpack_require__(8809).A;
          gmailInterceptor();
          setupGmonkeyHandler();
          extCorbWorkaroundPageWorld.init();
          xhrHelper();
          setupDataExposer();
          setupEventReemitter();
          setupErrorSilencer();
          setupCustomViewEventAssassin();
          setupPushStateListener();
          setupInboxCustomViewLinkFixer();
        } catch (err) {
          logger.error(err);
        } finally {
          if (oldDefine) {
            __webpack_require__.amdD = oldDefine;
          }
        }
      }
    })();
  })();
})();
