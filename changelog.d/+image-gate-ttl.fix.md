The PreSync image and volume gate Jobs now set `ttlSecondsAfterFinished: 86400`, so their finished pods are reaped after a day instead of accumulating.
