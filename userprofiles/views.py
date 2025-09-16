import jwt
from django.conf import settings
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.contrib.auth.models import User
from .serializers import UserSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.views import APIView

class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = (AllowAny,)
    serializer_class = UserSerializer

    # Override the default create method
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        
        # Generate a token for the user
        refresh = RefreshToken.for_user(user)
        token = str(refresh.access_token)
        
        # TODO: Send an email with a link like /verify-email/confirm/{token}
        print(f"--- VERIFICATION TOKEN FOR {user.email}: {token} ---")
        
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
    

class VerifyEmailView(APIView):
    """
    API endpoint to verify a user's email with a token.
    """
    permission_classes = (AllowAny,)

    def post(self, request, *args, **kwargs):
        token = request.data.get('token')
        if not token:
            return Response({'error': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Decode the token to get the user ID
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            user_id = payload['user_id']
            user = User.objects.get(id=user_id)

            if user.is_active:
                return Response({'message': 'Account already verified.'}, status=status.HTTP_200_OK)

            user.is_active = True
            user.save()
            return Response({'message': 'Email successfully verified!'}, status=status.HTTP_200_OK)

        except jwt.ExpiredSignatureError:
            return Response({'error': 'Activation link has expired.'}, status=status.HTTP_400_BAD_REQUEST)
        except (jwt.exceptions.DecodeError, User.DoesNotExist):
            return Response({'error': 'Invalid activation link.'}, status=status.HTTP_400_BAD_REQUEST)
