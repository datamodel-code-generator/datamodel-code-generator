--- server/README.md
+++ server/README.md (expected)
@@ -249,5 +249,3 @@
 After a take-over, the modules of `services` still import the server package they were written for, as in `from <old package>.services import ...`: update those imports to `server` yourself, since generation never rewrites them.
 Each generation prints the `uv add` command that adds the runtime dependencies of the package to your project;
 run it again when a new version of datamodel-code-generator changes them.
-
-Changed documentation.
