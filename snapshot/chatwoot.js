/* Chatwoot support widget (inbox "ERC Communities — Website").
 * DEFAULT OFF. To turn support on for visitors, set ENABLED to true and deploy.
 * The website token is public (it only identifies the inbox). */
(function () {
  var ENABLED = false;
  if (!ENABLED) return;

  var BASE_URL = "https://support.suncoastservers.com";
  window.chatwootSettings = {
    hideMessageBubble: false,
    position: "right",
    locale: "en",
    type: "standard",
    launcherTitle: "Chat with ERC Communities"
  };
  var g = document.createElement("script");
  var s = document.getElementsByTagName("script")[0];
  g.src = BASE_URL + "/packs/js/sdk.js";
  g.async = true;
  s.parentNode.insertBefore(g, s);
  g.onload = function () {
    window.chatwootSDK.run({ websiteToken: "UaMrcejZcZ7JTK4o3ztcqCmC", baseUrl: BASE_URL });
  };
})();
