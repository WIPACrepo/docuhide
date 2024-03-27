# docuhide
Migration from Docushare to Google Drive


## Example usage

First, we use the dump script to create a regular copy of the
docushare structure, using titles and the latest version of documents.

```
python3 dump.py <Collection ID> <Output Dir>
```

Then we can use `rclone` to upload to Google Drive.


