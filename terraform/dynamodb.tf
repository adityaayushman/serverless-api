resource "aws_dynamodb_table" "links" {
  name         = "${var.name}-links"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "slug"

  attribute {
    name = "slug"
    type = "S"
  }

  # Links created with expiresInDays carry an epoch-seconds expiresAt. DynamoDB
  # removes expired items in the background; the application also checks expiry
  # itself because that deletion can lag by hours.
  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  deletion_protection_enabled = var.table_deletion_protection
}
